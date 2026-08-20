from typing import Optional, Tuple, Union

from blitspersecond.common import Location
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
        sprite.present_at(*fighter.position, flip_x=fighter.facing_left)

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
    def flip_x(self) -> bool:
        """Facing. Mirrors the complete local geometry -- draw parts now,
        collision planes with them when sprite collision lands -- about the
        anchor's vertical axis. Never rewrites the asset."""
        return self._flip_x

    @flip_x.setter
    def flip_x(self, value: bool) -> None:
        self._flip_x = bool(value)
        self._engine._touch()

    @property
    def rotation(self) -> int:
        """Quarter-turns clockwise (0-3), about the anchor -- the D of the
        tile flip bits' H/V/D, arrived at the same way: UV corners cycle
        and mask views turn, the asset is never rewritten. Applied AFTER
        flip_x (mirror the local frame, then turn it), so a mirrored
        fighter and a rotated crate compose predictably. Values wrap: 4 is
        0, -1 is 3."""
        return self._rotation

    @rotation.setter
    def rotation(self, value: int) -> None:
        self._rotation = int(value) & 3
        self._engine._touch()

    def present_at(
        self,
        x: float,
        y: float,
        flip_x: Optional[bool] = None,
    ) -> None:
        """Position (and optionally face) this sprite in one call -- the
        per-tick line game code writes under an animation.

        Takes x and y separately; `position` reads back as a pair, so pass
        another sprite's straight through with `present_at(*other.position)`.
        """
        self.position = (x, y)
        if flip_x is not None:
            self.flip_x = flip_x

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
