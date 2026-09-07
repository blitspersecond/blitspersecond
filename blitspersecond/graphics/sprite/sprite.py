"""`Sprite`: one drawn thing, one layer, one collision identity."""

from __future__ import annotations

from math import floor
from typing import FrozenSet, Hashable, Iterator, Optional, Tuple, Union

import numpy as np

from blitspersecond.common import Location

from ..internal.layer import Layer
from ..internal.palette_texture import PaletteTexture
from ..orientation import Orientation
from .internal import PaletteRegisters
from .animation import Animation
from .batch import SpriteBatch
from .collision import SpriteCollision
from .frame import FramePlan
from .shader import program as sprite_program
from .presentation import NO_ASSEMBLY, PRIMARY, Chain, Presentation, Slots
from .sheet import Assembly, SpriteSheet


class _Slot0Channel:
    """One of the sprite's own colour registers, which are slot 0's.

    A descriptor rather than four delegating property pairs. It forwards by
    NAME to `Presentation`, so there is exactly one clamp and one docstring
    for each channel and the two surfaces cannot drift apart.
    """

    def __init__(self, name: str, doc: str) -> None:
        self._name = name
        self.__doc__ = doc

    def __get__(self, sprite: "Sprite", owner=None) -> float:
        if sprite is None:
            return self  # type: ignore[return-value]
        return getattr(sprite.primary, self._name)

    def __set__(self, sprite: "Sprite", value: float) -> None:
        setattr(sprite.primary, self._name, value)


class Sprite(Layer):
    """One drawn thing over one sheet -- and a `Layer`, so it IS its own place
    in the draw order.

        chun = Sprite(SpriteSheet("chun-li.sprite.json"))
        bps.layers.add(chun)
        chun.assembly = "stance-01"
        chun.position = (160, 200)

    ## There is exactly one Sprite per layer

    Not a convention to remember -- it is what the word has to mean. Kris:

        "by definition there is only one Sprite per layer -- you can't collide
        multiple Sprite's unless they have their own layer."

    Which is the engine's premise read back from this side: *a layer is one
    texture, one draw call, and one answerable collision identity.* Two things
    in one layer cannot be told apart by a collision question, because the
    OR'd bits do not say whose is whose. So two sprites are two layers, always
    -- and a mirror match is two `Sprite`s binding ONE `SpriteSheet`: shared
    pixels, one texture upload, separate identities, and each with its own
    palette register.

    **Spend a layer to gain an answerable identity.** A fireball in flight
    gets its own, because "did the fireball hit" has its own subject. That is
    self-limiting in an honest way, and it is why `Console` and `TileMap` are
    the same shape: reference had a `SpriteLayer` holding N `Sprite`
    instances, and fusing them is the third and last time this tree makes that
    collapse.

    ## What is drawn many times is not many sprites

    A shadow trail, Dobkeratops's tail segments, twenty-five thousand bullets:
    one identity, many quads. Those are **presentations** of one sprite, not
    sprites, and they arrive at the next step. What this class holds today is
    one presentation's worth of state, and that surface does not change when
    the rest lands -- `chun.position = (160, 200)` is what a lone sprite's
    author writes and will keep writing.

    ## What it owns, and what a presentation owns

    The **sheet**, the **origin** and (later) the **clip** are the sprite's,
    in every case. Pose, position, orientation, palette registers, colour
    registers and the two intent bits are **presentation-addressable** -- they
    have a value per presentation, and where they physically live is the next
    step's business. See `SPRITE_PORT.md` F5.

    ## It does not draw yet

    `_composite` is the base class's no-op. Adding one to a draw order is
    legal and shows nothing, exactly as `TileMap` did between its shell and
    its draw.
    """

    def __init__(self, sheet: SpriteSheet, tag: Optional[str] = None) -> None:
        if not isinstance(sheet, SpriteSheet):
            raise TypeError(
                f"a sprite draws from a SpriteSheet; got {type(sheet).__name__}. "
                f"Load one with SpriteSheet(path), or read a bare image as a "
                f"grid with SpriteSheet(image, w, h)."
            )
        super().__init__(tag=tag)
        # Assigned once, with no setter. The sheet is the identity's art and
        # rebinding it would make every retained thing below -- the pose, the
        # registers, later the stamped masks -- describe a sheet that is no
        # longer there. Want other art? That is another sprite, which is
        # another layer, which is another identity.
        #
        # It also holds the chain that keeps the pixels alive: sheet ->
        # tileset -> Atlas -> PixelBuffer, strong at every step, because
        # PixelBuffer interns WEAKLY and a layer holding only a texture id
        # would draw from a freed texture name some arbitrary time later.
        self._sheet = sheet

        # Assembly names are addressed by integer everywhere below this line:
        # a slot stores an id, and staging gathers rects by it. Built once
        # here rather than on the sheet because two sprites over one sheet is
        # a mirror match, and the dict is microseconds -- move it if
        # measurement ever asks.
        self._assembly_names: Tuple[str, ...] = tuple(sheet.assemblies)
        self._assembly_ids = {
            name: index for index, name in enumerate(self._assembly_names)
        }
        # How many quads each assembly is worth. Instanced presentations may
        # only ever hold a 1, but a BLANK pose is legal everywhere -- six of
        # Chun-Li's 3198 have no parts -- and must contribute nothing rather
        # than a zero-area quad, so staging filters on this.
        self._assembly_quads = np.array(
            [len(a.parts) for a in sheet.assemblies.values()], dtype=np.int32
        )
        # One slot to begin with, and it is slot 0. A sprite with no
        # presentations is not a thing, so this is the floor rather than a
        # default -- see `presentation.py`.
        self._default_palette = sheet.tilesets[
            sheet.primary_tileset
        ].default_palette
        self._slots = Slots(self._default_palette)
        #: The sprite's colour registers: RGB multipliers over whatever the
        #: palette produced. ONE set per sprite, reached through any of its
        #: presentations -- see `Presentation.red`. There is no alpha: a
        #: pixel is drawn or discarded.
        self._color = np.ones(3, dtype=np.float32)
        # Draw order, kept apart from identity: two arrays of slot ids with
        # the primary implicitly between them. See `presentation.py`.
        self._before = np.empty(0, dtype=np.int32)
        self._after = np.empty(0, dtype=np.int32)
        # Slot 0 alone gets a register per tileset, because slot 0 alone may
        # present art from more than one. Instanced slots keep their single
        # register in the per-slot array with everything else.
        # Banks this sprite has grown for itself, by tileset name. Empty for
        # almost every sprite: adding rows is what puts one here, and until
        # then the sheet's own bank is what draws -- so unextended siblings
        # share a bank object, and `PaletteTexture` keys on identity, so they
        # share its texture and its bind too.
        self._banks: dict = {}
        self._slot0_palettes = {
            name: tileset.default_palette
            for name, tileset in sheet.tilesets.items()
        }
        self._origin = Location(0, 0)
        # Two flags, because `visible` and `collide` invalidate different
        # things and a blinking sprite must not restamp a plane it could not
        # have changed. Nothing reads either yet.
        self._dirty = True
        self._collision_dirty = True
        # Built on the first render that needs it and dropped when collision
        # is switched off, so a layer nobody collides costs nothing for it.
        self._collision: Optional[SpriteCollision] = None
        # Retained across frames and rewritten in place, so a still scene
        # allocates nothing. Nothing reads it until the draw lands.
        self._plan = FramePlan()
        # Built on the first draw, not here: compiling a program and making
        # buffers needs a current GL context, and a sprite may be constructed
        # long before there is a window.
        self._batch: Optional[SpriteBatch] = None
        # The clip cursor, and one of the three things only the primary has.
        # `None` means nothing is playing, which is what `_update_animations`
        # tests --
        # reference kept a SET of animating instances because a layer held N
        # of them; one sprite has at most one clip, so the set is gone.
        self._animation: Optional[Animation] = None

    # -- the presentations -------------------------------------------------

    def __getitem__(self, slot: int) -> Presentation:
        """One presentation by SLOT ID -- its identity, not its place.

            held = chun.before.append()
            chun[held.slot] is held          # the same presentation

        This is an address. It says nothing about draw order, and `chun[3]`
        is not "the fourth thing drawn" -- that is `chun.before[3]`, which is
        an ordinary sequence index. Keeping the two apart is the point: a
        presentation can be moved in the order without its state moving, and
        its id outlives every reordering.

        `chun[0]` is the primary, which is the sprite itself.
        """
        return Presentation(self, slot)

    @property
    def before(self) -> Chain:
        """The presentations drawn BEFORE the sprite, and so behind it.

            ghosts = chun.before.extend(4)

        A trail lives here: `before[0]` draws first and is furthest back, so
        distance along the chain is depth. Ordinary sequence indexing, so
        `before[-1]` is the one nearest the sprite.
        """
        return Chain(self, after=False)

    @property
    def after(self) -> Chain:
        """The presentations drawn AFTER the sprite, and so in front of it.

            smear = chun.after.append()

        The unusual direction -- a forward smear, a muzzle flash over the art.
        For a swarm with no principal to speak of it is simply where the
        population lives, and `fountain.after.extend(25_000)` is one
        allocation and one concatenate.
        """
        return Chain(self, after=True)

    @property
    def primary(self) -> Presentation:
        """The sprite's own presentation -- slot 0, drawn between the chains.

        `chun.primary.position` and `chun.position` are one value in one
        place: the sprite's attributes ARE this presentation, which is why a
        lone sprite reads as though presentations do not exist.
        """
        return Presentation(self, PRIMARY)

    @property
    def presentations(self) -> Iterator[Presentation]:
        """Every presentation in draw order: before, the primary, then after.

        This IS the order the frame plan emits, and the order transparency
        depends on -- there is no sort anywhere, because the chains already
        are the answer.
        """
        return (
            Presentation(self, int(slot))
            for slot in (*self._before, PRIMARY, *self._after)
        )

    def assembly_id(self, key: Union[str, int, Tuple[int, int]]) -> int:
        """Resolve a pose name to the id a bulk write uses.

            swarm.assemblies[0::2] = fountain.assembly_id("blue")

        **Validated once, here.** The name is checked against the rules an
        instanced presentation lives by -- one part, on the sheet's primary
        tileset -- so assigning 25,000 of them is one check and one broadcast
        rather than 25,000 checks. That is also why this exists rather than a
        raw name-to-index dict: the bulk door has to be as safe as the setter,
        and it is, by checking the NAME instead of the elements.
        """
        name = self._sheet.assembly_name(key)
        self._refuse_unless_instanceable(
            name, self._sheet.assembly(key), "this id is for instanced use"
        )
        return self._assembly_ids[name]

    def _refuse_unless_instanceable(
        self, name: str, assembly: Assembly, where: str
    ) -> None:
        """The two rules an instanced presentation lives by.

        One place, because both doors into a pose -- the per-presentation
        setter and the bulk `assembly_id` -- must apply the identical rules,
        and two copies would be two chances to drift. `where` is how the
        caller describes itself, so the message names the actual mistake
        rather than a slot the bulk path does not have.
        """
        if len(assembly.parts) > 1:
            raise ValueError(
                f"assembly {name!r} has {len(assembly.parts)} parts, and only "
                f"the sprite's own presentation may hold a multipart pose "
                f"({where}). Every instanced presentation is one quad, which "
                f"is what lets four of them and twenty-five thousand share "
                f"one representation -- a multipart pose on each would cost "
                f"two texture binds per presentation, in draw order, with "
                f"nothing to amortise them."
            )
        primary = self._sheet.primary_tileset
        for part in assembly.parts:
            if part.tileset != primary:
                raise ValueError(
                    f"assembly {name!r} draws from tileset "
                    f"{part.tileset!r}, but instanced presentations draw only "
                    f"from this sheet's primary tileset {primary!r} "
                    f"({where}). That is what makes the whole instanced block "
                    f"one texture and one palette. Art belonging to another "
                    f"tileset is usually another sprite with its own "
                    f"collision identity."
                )

    def _inheritable_assembly(self) -> int:
        """The primary's pose id if a new presentation may legally hold it,
        else `NO_ASSEMBLY`.

        **The rule is exactly "inherit it if the setter would accept it"**, and
        it is written to stay that way: an instanced presentation refuses a
        multipart pose and refuses art from a tileset other than the primary
        one, so a template that is either cannot be copied down. Seeding one
        would plant state no setter would have allowed, and the refusal would
        then fire on some later, unrelated write.

        A BLANK pose IS inherited, because a blank is accepted everywhere --
        presenting nothing is a state, not a failure, and six of Chun-Li's
        3198 assemblies are one. An earlier version rejected it here, which
        made the seeder and the setter disagree about the same pose: the
        picture was identical either way, but the ghost read back `None`
        instead of the pose's name.
        """
        index = int(self._slots.assembly[PRIMARY])
        if index < 0 or self._assembly_quads[index] > 1:
            return NO_ASSEMBLY
        parts = self._sheet.assemblies[self._assembly_names[index]].parts
        if parts and parts[0].tileset != self._sheet.primary_tileset:
            return NO_ASSEMBLY
        return index

    def _presented_assembly(self) -> Optional[Assembly]:
        """Slot 0's pose as an `Assembly`, or None if it presents nothing."""
        name = self.primary.assembly
        return None if name is None else self._sheet.assembly(name)

    def _sync(self) -> None:
        """Rebuild the frame plan, and the collision plane, if anything moved.

        Private, and called from `_composite` -- not a phase a game runs.
        Reference exposed this as `prepare()` and called it from exactly one
        place: the first line of its own composite. Same finding and same fix
        as the other two pillars.

        **The masks are rendered with the frame**, which is the engine's own
        cycle: clear, read input, run the game, render the frame and the
        masks. So a collidable sprite leaves the compose with its plane
        already composed and the tick that asks about it spends nothing.

        Two flags, because the two projections are invalidated by different
        things. `visible` moves the draw and not the plane; `collide` moves
        the plane and not the draw. A blinking sprite must not recompose a
        silhouette it could not have changed.
        """
        if self._dirty:
            self._plan.sync(self)
            self._dirty = False

    def _settle_collision(self) -> None:
        """Recompose the plane if a presentation moved. The `Layer` hook.

        Split out of `_sync` so a HIDDEN sprite layer still reaches it: the
        plan is a draw projection and belongs behind `enabled`, the plane is a
        world one and does not.
        """
        if self._collidable and self._collision_dirty:
            self._collision_mask().compose()
            self._collision_dirty = False

    # -- collision ---------------------------------------------------------

    def _collision_refusal(self) -> Optional[str]:
        """A sprite can always collide -- and does not have to.

        There is nothing to refuse: both doors into a sheet reject a
        non-indexed surface outright (*"a sprite sheet is an indexed (P)
        surface; use a TileMap for RGB(A) pictures"*), so the palettised rule
        the base states is already met before a `Sprite` exists. Reference
        expressed this as a constant `True`; the reason it could is here.

        Participation is a different question and is asked twice more:
        `collidable` switches the layer, and `collide` decides each
        presentation. **All or nothing per presentation** -- a multipart pose
        contributes every part or none, because a part carries no flag of its
        own.
        """
        return None

    @Layer.collidable.setter  # type: ignore[misc]
    def collidable(self, value: bool) -> None:
        Layer.collidable.fset(self, value)  # type: ignore[misc]
        if value:
            # Whatever was composed last is about to be stale -- the sprite
            # kept moving while collision was off.
            self._collision_dirty = True
        else:
            self._collision = None

    def _collision_mask(self) -> SpriteCollision:
        """This layer's plane, built on first use.

        Sized from the sheet, so building one walks its rects; that happens on
        the first render that needs it rather than at construction, because a
        sprite nobody collides should not pay for a scan or for 40KB.
        """
        if self._collision is None:
            self._collision = SpriteCollision(self)
        return self._collision

    @property
    def planes(self) -> FrozenSet[Hashable]:
        """Every named collision plane this sheet's assemblies author.

        The sheet's vocabulary, precalculated across every assembly -- so a
        move that only some frames have an `attack` box on still declares
        `attack`, and asking on a frame without one answers no rather than
        raising. Empty is the common case and not a degenerate one: it is the
        R-Type heritage path, where the silhouette is all there is.
        """
        return self._sheet.planes

    def collision_plane(self, name: Optional[Hashable] = None) -> np.ndarray:
        """One composed plane: the silhouette, or a name from `planes`.

        `collider()` has already checked the name against the vocabulary, so
        an unknown one never reaches here.

        **A read composes nothing.** The plane is whatever `_sync` left at the
        last commit, which is the finalised state of the frame that was
        painted -- and a sprite roster has no earlier moment that means
        anything, because nothing knows every member is drawn until every
        member is drawn. Recomposing here would answer about a frame nobody
        has seen and bill the game for building it; `_collision_dirty` is
        therefore read at commit and nowhere else.
        """
        return self._collision_mask().plane(name)

    def _composite(self) -> None:
        """Draw this sprite: sync, upload, then one draw per run.

        Called by the compositor with the framebuffer bound and a 1:1 ortho
        already set. Sets no projection and no viewport -- those belong to the
        compositor, and the present pass owns the flip that puts y=0 at the
        top of the screen.

        The run loop is the pillar. A tilemap spends its single bind on a
        uniform grid; this spends binds on ordered composition, and the frame
        plan has already worked to make that as few as it honestly can -- one
        run for a whole population, two when a multipart pose crosses to
        another texture.
        """
        self._sync()
        plan = self._plan
        if not plan.quads:
            return
        if self._batch is None:
            self._batch = SpriteBatch(sprite_program(), plan.quads)
        batch = self._batch
        program = batch.program
        program.use()
        program["sprite_texture"] = 0
        # Uploaded every rebuild rather than tracked per quad: a sprite's UVs
        # change whenever a pose or an orientation does. `_sync` is
        # dirty-gated, so a still scene reaches here having changed nothing
        # and re-uploads the same bytes; the frames that matter are the ones
        # that moved.
        batch.upload(plan.data, plan.quads)
        # No blend state here. It is COMPOSITING state, read per layer from
        # `Layer.blend` by `internal/display.py`, and off by default -- see
        # that loop for why off is the model rather than a saving.
        batch.begin()
        bound_texture = None
        bound_palette = None
        # None rather than 0: generic attribute values are context state and
        # outlive a pass, so the row another layer left behind is not known
        # to be this layer's first row.
        bound_row = None
        for buffer, bank, row, colors, first, count in plan.runs:
            # Touch the texture BEFORE binding either unit: `.texture` and
            # `PaletteTexture.get` blit into whatever unit is active, so they
            # must not run between the unit assignments inside `draw_run`.
            texture = buffer.texture
            bound_texture, bound_palette, bound_row = batch.draw_run(
                texture,
                PaletteTexture.get(bank),
                row,
                colors,
                first,
                count,
                bound_texture,
                bound_palette,
                bound_row,
            )
        batch.end()
        program.stop()

    def _touch(self) -> None:
        """Presentation changed: both projections need rebuilding."""
        self._dirty = True
        self._collision_dirty = True

    # -- the sheet ---------------------------------------------------------

    @property
    def sheet(self) -> SpriteSheet:
        """The art this sprite draws from. Set once; there is no setter."""
        return self._sheet

    # -- the pose ----------------------------------------------------------

    @property
    def assembly(self) -> Optional[str]:
        """The name of the pose being presented, or None if none is.

        Assign a name, or -- on a sheet read as a grid -- a cell index or
        `(col, row)` pair, the way an autogrid `Atlas` is addressed. It reads
        back as the name either way.

        `None` presents nothing, and so does a deliberate blank pose: six of
        Chun-Li's 3198 assemblies have no parts at all. Presenting nothing is
        a state, not a failure.
        """
        return self.primary.assembly

    @assembly.setter
    def assembly(self, key: Union[str, int, Tuple[int, int], None]) -> None:
        # Writing a pose by hand TAKES THE SPRITE OFF ITS CLIP. Two things
        # writing one pose every tick is a fight, and the explicit write wins:
        # a game that has decided what to show should not have the sequencer
        # overwrite it a moment later, on a frame boundary, invisibly.
        self._animation = None
        self.primary.assembly = key

    # -- playback ----------------------------------------------------------

    def play(
        self,
        clip: str,
        frame: int = 0,
        loop: Optional[bool] = None,
    ) -> None:
        """Play one of the sheet's clips. The only animation verb there is.

            chun.play("walk")
            chun.play("attack", loop=False)
            chun.play(chun.playing, frame=chun.animation.frame)   # hitstop

        Start, cancel, interrupt and reissue are one act, because each of the
        others would be this with a different name and a game would then have
        to decide which it meant. It advances itself on the fixed tick from
        here; nothing needs calling per frame to keep it running.

        **It applies its first frame immediately**, so a `play()` issued in
        this tick's game callback shows this tick, rather than a frame of the
        previous pose leaking through.

        `loop` defaults to what this sprite was last told, and to looping. A
        one-shot holds its last frame when it finishes and reports `done`.

        The clip is the SPRITE's, never a presentation's -- presentations are
        posed by hand. A trail ghost showing a snapshot of the past is exactly
        a thing that must not be playing anything.
        """
        animation = self._animation
        if animation is None:
            animation = self._animation = Animation(self._sheet)
        animation.play(clip, frame=frame, loop=loop)
        self._apply(animation)

    @property
    def animation(self) -> Optional[Animation]:
        """The clip cursor, or None if nothing is playing.

        Reach through it for `frame`, `loop` and `done`. The common case never
        needs it: `play()` and `playing` are the whole surface most games use.
        """
        return self._animation

    @property
    def playing(self) -> Optional[str]:
        """The name of the clip being played, or None.

        `if chun.playing != "walk": chun.play("walk")` is the idiom -- `play`
        restarts, so a game guards it rather than calling it every tick.
        """
        return None if self._animation is None else self._animation.name

    @property
    def done(self) -> bool:
        """Whether a one-shot has finished. False if nothing is playing, and
        never true for a looping clip."""
        return self._animation is not None and self._animation.done

    def prepare(self) -> None:
        """The `Layer` BEGIN slot. This pillar's work is its animations."""
        self._update_animations()

    def _update_animations(self) -> None:
        """One tick of playback.

        Costs one `is None` test on a sprite with no clip, which is what makes
        a swarm of 25,000 presentations that animates nothing free.
        """
        if self._animation is None:
            return
        self._animation.update()
        self._apply(self._animation)

    def _apply(self, animation: Animation) -> None:
        """Put the cursor's pose on the primary WITHOUT cancelling the clip.

        Deliberately not `self.assembly = ...`, which is the cancel: the
        sequencer would otherwise switch itself off the moment it advanced.
        Straight into the slot because a clip's frames were validated against
        the sheet at load, so there is nothing left to check.
        """
        name = animation.assembly
        if name is None:
            return
        self._slots.assembly[PRIMARY] = self._assembly_ids[name]
        self._touch()

    # -- where it is -------------------------------------------------------

    @property
    def position(self) -> Location:
        """Where the sprite's `origin` lands. **Whole pixels, always.**

            chun.position = (160, 200)
            chun.x += 1

        The GPU would take a float and interpolate one happily. The engine
        will not: this is a pixel art engine, a sprite occupies a whole pixel,
        and `Location.x` is an `int`. A float assigned here is floored on the
        way in -- so `x` reads back as what was stored, not as what was sent,
        and the draw and the collision stamp read the same number rather than
        each flooring their own copy.

        **The consequence, stated plainly: `sprite.x += 0.4` never moves the
        sprite.** Each write floors to the same integer, so the fraction is
        gone before the next one arrives. A sprite moving at less than a pixel
        a tick needs the fraction kept somewhere that is not the sprite:

            self.fx += 0.4          # the game's accumulator
            sprite.x = self.fx      # floored here, once

        The engine does not keep that accumulator, because it cannot know what
        it means -- velocity, interpolation between fixed ticks, and an easing
        curve want it integrated in three different places, and picking one
        would make the other two fight it.

        Flooring, not truncating: `-0.5` goes to `-1`, so a sprite crossing
        zero leftward moves at the same rate as one crossing it rightward.
        `int()` would stall it for a pixel at the origin.
        """
        return self.primary.position

    @position.setter
    def position(self, value: Union[Tuple[float, float], Location]) -> None:
        self.primary.position = value

    @property
    def x(self) -> int:
        """Horizontal position in whole pixels. See `position`."""
        return self.primary.x

    @x.setter
    def x(self, value: float) -> None:
        self.primary.x = value

    @property
    def y(self) -> int:
        """Vertical position in whole pixels. See `position`."""
        return self.primary.y

    @y.setter
    def y(self, value: float) -> None:
        self.primary.y = value

    # -- the reference point -----------------------------------------------

    @property
    def origin(self) -> Location:
        """The sprite's own reference point: where `position` puts it, and the
        point it mirrors and turns about.

            drawn at  =  position  +  offset  -  origin

        Two separate things place a sprite's art, and keeping them apart is
        what makes both explainable. The ORIGIN is the sprite's and defaults
        to `(0, 0)` -- no halfway point is invented for you, because a default
        you cannot predict is worse than one you have to change. The OFFSET is
        the *pose's*, carried by the asset, and says where that particular
        art sits relative to the point.

        Chun-Li is the offset half: her origin is `(0, 0)` and it lands
        between her feet, on the centre line of her push box, while 958
        distinct per-frame offsets hang the art around it as she leans and
        strides. Mirroring about that point turns her round and leaves her
        body where it was -- which is why two fighters' `x` values are a real
        distance whatever their limbs are doing.

        A grid sheet is the origin half: every cell is the same size and the
        art fills it, so there are no per-pose offsets. Left at `(0, 0)` the
        sprite draws from its cell's top-left and flips about that corner,
        which moves it a full width sideways. A sprite that turns on the spot
        wants `origin = sprite.centre`.
        """
        return self._origin

    @origin.setter
    def origin(self, value: Union[Tuple[int, int], Location]) -> None:
        self._origin = Location(floor(value[0]), floor(value[1]))
        self._touch()

    @property
    def centre(self) -> Location:
        """The middle of the pose currently presented, in the local frame --
        the value to hand `origin` when a sprite should turn on the spot:

            walker.origin = walker.centre

        **Read it once, at setup.** It is derived from the pose being shown,
        so on uniform grid art every pose gives the same answer, but an
        authored sheet's poses differ frame to frame and reassigning this
        every tick would make the sprite wander.

        Raises if nothing is presented: there is no art to find the middle of.
        """
        assembly = self._presented_assembly()
        if assembly is None or not assembly.parts:
            raise ValueError(
                "no pose is presented, so there is no art to centre on -- set "
                "an assembly first"
            )
        tilesets = self._sheet.tilesets
        left = top = right = bottom = None
        for part in assembly.parts:
            rect = tilesets[part.tileset].atlas.rect(part.tile)
            x0, y0 = part.offset.x, part.offset.y
            x1, y1 = x0 + rect.width, y0 + rect.height
            left = x0 if left is None else min(left, x0)
            top = y0 if top is None else min(top, y0)
            right = x1 if right is None else max(right, x1)
            bottom = y1 if bottom is None else max(bottom, y1)
        assert left is not None and top is not None
        assert right is not None and bottom is not None
        return Location((left + right) // 2, (top + bottom) // 2)

    # -- how it is turned --------------------------------------------------

    @property
    def orientation(self) -> Orientation:
        """How the sprite is turned: one of the eight axis-aligned
        orientations, the same vocabulary a tile placement speaks.

            enemy.orientation = Orientation.FLIP_H       # facing
            crate.orientation = Orientation.ROT_90

        Stored as an `Orientation` rather than as a bool beside a turn count.
        Reference kept `flip_x` and `rotation` separately on the instance and
        an `Orientation` value in the pool, which is one vocabulary in two
        storages -- and the pool was the one that was right, because the
        eight-variant caches this feeds are already indexed by orientation.

        The mirror and the turn happen about `origin`, and the collision
        planes turn with the art: draw and planes share one formula precisely
        so they cannot drift.

        **A caveat that lands with the draw, not here.** `applies_to` says a
        rectangular source may only take the four shape-preserving
        orientations -- but its reasoning is about a cell returning to its
        grid and overflowing its neighbours, and a sprite has neither. Sprites
        turn arbitrary rectangles routinely. See `SPRITE_PORT.md` F7.
        """
        return self.primary.orientation

    @orientation.setter
    def orientation(self, value: Orientation) -> None:
        self.primary.orientation = value

    # -- what it is drawn with ---------------------------------------------

    def _bank(self, tileset: str):
        """The bank this sprite draws `tileset` through.

        Its own if it has grown one, otherwise the sheet's. The draw path reads
        this once per CHAIN -- `FramePlan` treats the bank as constant across
        one, and depends on that for the bind -- so a sprite with its own bank
        costs one more palette texture and one more bind, and a trail of 25,000
        ghosts still costs one of each because a trail is one sprite.
        """
        bank = self._banks.get(tileset)
        return self._sheet.tilesets[tileset].palettes if bank is None else bank

    @property
    def palettes(self) -> PaletteRegisters:
        """Per-tileset palette selection: `chun.palettes["character"] = 3`.

        Defaults come from each tileset's `default_palette`, so a player's
        colour pick sets the character register while effects stay pinned.
        """
        return PaletteRegisters(self, PRIMARY)

    red = _Slot0Channel(
        "red",
        """Red multiplier, 0.0..1.0 -- one of four colour registers.

        `red`/`green`/`blue` toward 0 fade toward black and unevenly tint;
        `alpha` toward 0 fades out. They multiply whatever the palette
        produced, so they are a presentation decision laid over a colour
        decision, not a second palette.

        This is where a shadow trail's fade comes from, and why the registers
        are presentation-addressable rather than sprite-wide: the whole effect
        is each ghost carrying a different value.
        """,
    )
    green = _Slot0Channel("green", "Green multiplier, 0.0..1.0. See `red`.")
    blue = _Slot0Channel("blue", "Blue multiplier, 0.0..1.0. See `red`.")
    alpha = _Slot0Channel("alpha", "Gone -- see the error it raises.")

    # -- being seen, and being there ---------------------------------------

    @property
    def visible(self) -> bool:
        """Whether the quad is sent. Nothing else.

        `visible` and `collide` are **two independent bits over one
        presence**, not a gate and a modifier, because being seen and being
        there are different questions and a game needs all four answers:

            visible  collide
              True     True    the ordinary case
              True     False   a GHOST -- an afterimage or a trail segment,
                               drawn but not there
              False    True    a CLOAK -- an invisible wall, a mine, a
                               Predator: there but not drawn
              False    False   PARKED -- kept and cheap, out of both passes

        They dirty different things, which is the same claim stated as cost:
        this invalidates staging only, so a blinking sprite never restamps a
        collision plane it could not have changed.

        Until 2026-08-20 stamping required both, which made the cloak
        inexpressible and quietly made `visible=False` mean *gone* -- so
        parking an effect by hiding it worked, and hiding a still-solid thing
        did not.
        """
        return self.primary.visible

    @visible.setter
    def visible(self, value: bool) -> None:
        self.primary.visible = value

    @property
    def collide(self) -> bool:
        """Whether this presence stamps the layer's collision planes.

        Independent of `visible` -- see the two-bit note there -- so a cloaked
        sprite still hits and a ghost still does not. Toggling this dirties
        the masks alone.

        Nothing reads it yet: collision is blocked behind the shared
        vocabulary, which is not sprite work. It lands now as STATE because
        retrofitting the two bits later would mean revisiting every setter.
        """
        return self.primary.collide

    @collide.setter
    def collide(self, value: bool) -> None:
        self.primary.collide = value

    def __repr__(self) -> str:
        pose = self.primary.assembly or "nothing"
        x, y = self.position
        return f"<Sprite {pose!r} at ({x}, {y}) [{self._tag}]>"
