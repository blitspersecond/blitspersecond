from typing import Optional, Tuple, Union

from blitspersecond.common import Location
from blitspersecond.graphics.common import (
    Orientation,
    as_flip_rotation,
    from_flip_rotation,
)
from blitspersecond.resources.sprite_sheet import AssemblySpec

from .animation import Animation


class PaletteRegisters(dict):
    """A sprite's per-tileset palette selection: tileset name -> table index.

    The hardware OBJ model made a mapping: each instance carries one register
    per tileset its sheet draws, seeded from the tileset's default_palette
    (the mirror match is two sprites writing different values into the same
    register name). Writes validate against the sheet and re-plan the
    engine's runs; reads are plain dict reads.
    """

    def __init__(self, engine, defaults) -> None:
        super().__init__(defaults)
        self._engine = engine

    def __setitem__(self, tileset: str, index: int) -> None:
        tilesets = self._engine.sheet.tilesets
        if tileset not in tilesets:
            raise KeyError(f"unknown tileset {tileset!r}")
        index = int(index)
        n = len(tilesets[tileset].palettes)
        if not 0 <= index < n:
            raise ValueError(
                f"palette {index} out of range (tileset {tileset!r} has {n})"
            )
        super().__setitem__(tileset, index)
        self._engine._touch()


class Sprite:
    """One instance: all per-presentation state, constructed against the
    engine (`engine.sprite("stance-01")` is the mainline).

    A sprite owns no move state, velocity, or effect lifetime -- game code
    selects what it presents and where:

        sprite.play("jump")
        sprite.position = fighter.position
        sprite.orientation = fighter.facing

    or, driving the pose directly, with no clip involved:

        sprite.assembly = "jump-02"

    It does own a clip once `play()` gives it one, and that clip advances on
    the game tick without the caller doing anything per-tick. That is the
    whole of its relationship with time: it holds no wall clock, and a tick
    is a fixed 1/fps step from the engine loop. The layer still never reads
    the sheet's animation data -- drawing sees `_assembly` and nothing else.

    The game keeps initiation, which is where the decisions are: `play()`
    again is the cancel, the interruption and the reissue. Branching,
    transitions, hitstop, root motion and effect spawning are game code
    above this line, per docs/SPRITES.md.

    What lives here, and why here (docs/SPRITES.md pins each): `assembly`
    (which pose the sheet presents), `position` (pixel-space, caller
    supplied), `flip_x` (facing -- mirrors draw parts and collision planes
    together), `palettes` (per-tileset registers; the mirror match
    proves they can't live on the shared engine), colour/alpha registers
    (the layer colour registers, per-quad here -- ghost trails dim through
    these), `visible` (the display intent every engine has) and `collide`
    (participate in the layer's retained planes, or be display-only).
    """

    def __init__(
        self,
        engine,
        assembly: Optional[Union[str, int, Tuple[int, int]]] = None,
        *,
        _z: int = 0,
    ) -> None:
        # Deliberately duck-typed (no isinstance): the engine import would be
        # circular, and the factory is the documented construction path.
        self._engine = engine
        self._assembly_name: Optional[str] = None
        self._assembly: Optional[AssemblySpec] = None
        self._xy = [0.0, 0.0]
        self._origin = Location(0, 0)
        self._animation: Optional[Animation] = None
        self._flip_x = False
        self._rotation = 0
        self._visible = True
        self._collide = True
        self._color = [1.0, 1.0, 1.0, 1.0]  # r, g, b, a multipliers
        self._palettes = PaletteRegisters(
            engine,
            {
                name: ts.default_palette
                for name, ts in engine.sheet.tilesets.items()
            },
        )
        if assembly is not None:
            self.assembly = assembly
        engine._adopt(self, _z)

    # -- pose --------------------------------------------------------------

    @property
    def assembly(self) -> Optional[str]:
        """The name of the pose the sheet presents for this sprite. None
        (never set) presents nothing -- as does a deliberate blank pose.

        Assign a name, or -- on a sheet built by `SpriteSheet.grid()` -- a
        cell index or `(col, row)` pair, the way a TileAtlas is addressed.
        Reads back as the name either way.

        Assigning by hand takes the sprite off any clip it was playing: two
        things writing one pose every tick is a fight, and the explicit write
        is the one that means it."""
        return self._assembly_name

    @assembly.setter
    def assembly(self, key: Union[str, int, Tuple[int, int]]) -> None:
        self.stop()
        self._set_assembly(key)

    def _set_assembly(self, key: Union[str, int, Tuple[int, int]]) -> None:
        """Write the pose without disturbing playback -- the path the tick's
        advance uses, so advancing a clip does not cancel it."""
        sheet = self._engine.sheet
        name = sheet.assembly_name(key)
        spec = sheet.assemblies.get(name)
        if spec is None:
            raise KeyError(f"unknown assembly {name!r}")
        self._assembly_name = name
        self._assembly = spec
        self._engine._touch()

    # -- playback ----------------------------------------------------------

    def play(
        self,
        name: str,
        loop: bool = True,
        frame: int = 0,
    ) -> "Animation":
        """Play a clip from the sheet. The game's ONE animation verb.

            kid.play("run")                 # loops
            kid.play("attack", loop=False)  # one-shot, holds its last frame

        The clip advances itself once per game tick from here on -- there is
        no per-tick call to make and no pose to copy across. What the game
        keeps is initiation: `play()` again is the cancel, the interruption
        and the reissue, all one primitive. A mid-clip cancel is
        `play(other)`; resuming somewhere specific is `play(name,
        frame=n)`; pinning a frame (hitstop) is reissuing the frame you are
        on, since setting a frame restarts its hold.

        Returns the Animation, so `.done`, `.frame` and `.name` are there
        when a state machine wants them -- state machines stay game code.
        """
        animation = self._animation
        if animation is None:
            self._animation = animation = Animation(
                self._engine.sheet, name, frame, loop
            )
        else:
            animation.play(name, frame, loop)
        self._engine._animating.add(self)
        self._set_assembly(animation.assembly)
        return animation

    def stop(self) -> None:
        """Stop advancing, holding the current pose. Not a pause -- the clip
        is dropped; `play()` is how anything starts again."""
        self._animation = None
        self._engine._animating.discard(self)

    @property
    def animation(self) -> Optional["Animation"]:
        """The clip this sprite is playing, or None. Read `.done` off it for
        one-shots; a state machine reacting to that is game code."""
        return self._animation

    def _advance(self) -> None:
        """One tick of playback: advance the clip, then present its frame.
        Called by the layer, which the engine loop calls once per game tick
        after the game's own tick has had its say -- so a `play()` issued
        this tick starts on this tick's frame."""
        animation = self._animation
        if animation is None:
            return
        animation.update()
        if animation.assembly != self._assembly_name:
            self._set_assembly(animation.assembly)

    # -- origin (the draw point, and the pivot) ----------------------------

    @property
    def origin(self) -> Location:
        """The sprite's own reference point: where `position` puts it, and
        the point `flip_x` mirrors about and `rotation` turns around.

        Two separate things place a sprite's art, and keeping them apart is
        what makes both explainable:

            drawn at   =   position  +  offset  -  origin

        The ORIGIN is the sprite's, and it defaults to `(0, 0)` -- the art's
        own top-left. No halfway point is invented for you, because a default
        you cannot predict is worse than one you have to change. The OFFSET
        is the *frame's*, carried by the asset, and says where this
        particular pose's art sits relative to that point.

        Chun-Li is the offset half: her origin is (0, 0) and it lands between
        her feet, on the centre line of her push box, while 958 distinct
        per-frame offsets hang the art around it as she leans and strides.
        Mirroring about that point turns her round and leaves her body
        exactly where it was -- which is why two fighters' `x` values are a
        real distance no matter what their limbs are doing.

        A grid sheet is the origin half: every cell is the same size and the
        art is drawn to fill it, so there are no frame offsets at all. Left
        at (0, 0) the sprite draws from its cell's top-left, and flipping
        mirrors about that corner -- which moves it a full width sideways,
        because mirroring about a corner is what that means. A sprite that
        turns around wants `origin = sprite.centre`, and then it turns on
        the spot.

        Moving the origin moves the draw point with it, necessarily: they
        are the same point. `origin = centre` means `position` now says
        where the sprite's centre goes.
        """
        return self._origin

    @origin.setter
    def origin(self, value: Union[Tuple[int, int], Location]) -> None:
        self._origin = Location(int(value[0]), int(value[1]))
        self._engine._touch()

    @property
    def centre(self) -> Location:
        """The middle of the current pose's art, in the sprite's local frame
        -- the value to hand `origin` when a sprite needs to turn on the
        spot rather than swing about its corner:

            walker.origin = walker.centre

        Derived from the pose currently presented, so read it once at setup
        rather than per tick: on uniform grid art every cell gives the same
        answer, but an authored sheet's poses differ frame to frame and a
        per-tick reassignment would make the sprite wander. Raises if
        nothing is presented, since there is no art to find the middle of.
        """
        if self._assembly is None or not self._assembly.parts:
            raise ValueError(
                "no pose is presented, so there is no art to centre on -- "
                "set an assembly (or play a clip) first"
            )
        tilesets = self._engine.sheet.tilesets
        left = top = right = bottom = None
        for part in self._assembly.parts:
            rect = tilesets[part.tileset].tiles[part.tile]
            x0, y0 = part.offset.x, part.offset.y
            x1, y1 = x0 + rect.width, y0 + rect.height
            left = x0 if left is None else min(left, x0)
            top = y0 if top is None else min(top, y0)
            right = x1 if right is None else max(right, x1)
            bottom = y1 if bottom is None else max(bottom, y1)
        assert left is not None and top is not None
        assert right is not None and bottom is not None
        return Location((left + right) // 2, (top + bottom) // 2)

    # -- position (pixel-space; floats held, floored at draw) --------------

    @property
    def position(self) -> Location:
        return Location(int(self._xy[0]), int(self._xy[1]))

    @position.setter
    def position(self, value: Union[Tuple[float, float], Location]) -> None:
        self._xy[0] = float(value[0])
        self._xy[1] = float(value[1])
        self._engine._touch()

    @property
    def x(self) -> float:
        return self._xy[0]

    @x.setter
    def x(self, value: float) -> None:
        self._xy[0] = float(value)
        self._engine._touch()

    @property
    def y(self) -> float:
        return self._xy[1]

    @y.setter
    def y(self, value: float) -> None:
        self._xy[1] = float(value)
        self._engine._touch()

    @property
    def orientation(self) -> Orientation:
        """How the sprite is turned: one of the eight axis-aligned
        orientations, the same vocabulary a tile placement uses.

            enemy.orientation = Orientation.FLIP_H          # facing
            crate.orientation = Orientation.ROT_90
            icon.orientation = Orientation.FLIP_V

        This is the state. There used to be a `flip_x` bool beside it, and
        it was the reason this vocabulary was needed: one axis privileged
        into a name, with the other two generators reachable only by
        composition -- a vertical flip was `flip_x=True, rotation=2`, a
        transpose was `flip_x=True, rotation=3`. Both findable only if you
        already knew. `Orientation.FLIP_V` says it instead, so the bool had
        nothing left to offer that a named constant does not.

        `rotation` below remains a view onto the turn, because a number of
        quarter-turns is a quantity you count rather than a transform you
        name -- and it composes: the mirror applies first, then the turn.

        Whatever it is set to, the mirror and the turn happen about the
        sprite's `origin`, and the collision planes turn with the art -- draw
        and planes share one orientation formula precisely so they cannot
        drift.
        """
        return from_flip_rotation(self._flip_x, self._rotation)

    @orientation.setter
    def orientation(self, value: Orientation) -> None:
        self._flip_x, self._rotation = as_flip_rotation(Orientation(value))
        self._engine._touch()

    @property
    def rotation(self) -> int:
        """Quarter-turns clockwise (0-3) -- the turn half of `orientation`,
        and the other convenience over it. Values wrap: 4 is 0, -1 is 3.

        Applied AFTER the mirror (mirror the local frame, then turn it), so a
        mirrored fighter and a rotated crate compose predictably, and so the
        decomposition is unique.
        """
        return self._rotation

    @rotation.setter
    def rotation(self, value: int) -> None:
        self._rotation = int(value) & 3
        self._engine._touch()

    # -- visibility / collision intent -------------------------------------

    @property
    def visible(self) -> bool:
        return self._visible

    @visible.setter
    def visible(self, value: bool) -> None:
        self._visible = bool(value)
        self._engine._touch()

    @property
    def collide(self) -> bool:
        """Participate in the layer's collision planes, or be display-only
        (afterimage ghosts ride their owner's layer with collide=False).
        Stored per-presentation state; consumed when sprite collision lands."""
        return self._collide

    @collide.setter
    def collide(self, value: bool) -> None:
        self._collide = bool(value)
        self._engine._collision_dirty = True

    # -- palette registers --------------------------------------------------

    @property
    def palettes(self) -> PaletteRegisters:
        """Per-tileset palette selection: `sprite.palettes["character"] = 3`.
        Defaults come from each tileset's default_palette, so a player's
        colour pick sets the character register while effects stay pinned."""
        return self._palettes

    # -- colour registers (0.0..1.0 multipliers, default 1.0) ---------------
    # The layer colour registers, per-quad here: rgb toward 0 fades to
    # black, alpha toward 0 fades out, uneven rgb tints. A ghost trail is
    # these registers on collide=False instances.

    @property
    def red(self) -> float:
        return self._color[0]

    @red.setter
    def red(self, value: float) -> None:
        self._color[0] = min(max(float(value), 0.0), 1.0)
        self._engine._touch()

    @property
    def green(self) -> float:
        return self._color[1]

    @green.setter
    def green(self, value: float) -> None:
        self._color[1] = min(max(float(value), 0.0), 1.0)
        self._engine._touch()

    @property
    def blue(self) -> float:
        return self._color[2]

    @blue.setter
    def blue(self, value: float) -> None:
        self._color[2] = min(max(float(value), 0.0), 1.0)
        self._engine._touch()

    @property
    def alpha(self) -> float:
        return self._color[3]

    @alpha.setter
    def alpha(self, value: float) -> None:
        self._color[3] = min(max(float(value), 0.0), 1.0)
        self._engine._touch()
