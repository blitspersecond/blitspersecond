"""Stream-backed Input, driven in-process: python tests/test_pitch_black.py

The lesson this test exists to not forget: to exercise keyboard input,
dispatch events onto bps.events yourself -- the exact seam the window feeds
(`on_text`, `on_text_motion`) -- from a scripted (tick, event) list inside
the main loop. It is deterministic, runs anywhere, and needs no xdotool /
XTEST / remote-desktop portal consent, all of which proved flaky against a
live desktop. Save desktop automation for true end-to-end window tests.

This doubles as an Input session test. The private handler journals each event;
the active Input replays editing and the demo renders its value. Events
dispatched from a game callback are snapshotted at the NEXT tick's update.

The engine is a singleton (one GL context per process), so the whole
scripted session lives in one test. The session closes itself by
dispatching on_close, with a tick cap as the dead-man's switch so a broken
script can't leave the window open.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyglet.window import key as pyglet_key

from blitspersecond import BlitsPerSecond
from blitspersecond.console import Console
from blitspersecond.graphics.glyph import CharSet, GlyphEngine
from blitspersecond.input import Ports, kbm, key, keyboard, mouse

from tests.examples.pitch_black import (
    INTRO,
    NARRATION,
    OUTLINE,
    Game,
    PromptView,
)

TICK_CAP = 300  # dead-man's switch: never leave the window open


def grid_lines(console: Console) -> list[str]:
    """The console cell grid as stripped ASCII lines."""
    return [
        "".join(
            chr(code) if 32 <= code < 127 else " "
            for code in (
                console.glyphs.cell[x, y] for x in range(console.cols)
            )
        ).rstrip()
        for y in range(console.rows)
    ]


def test_stream_input_session():
    bps = BlitsPerSecond()
    assert bps.kbm is kbm
    assert bps.keyboard is keyboard
    assert bps.mouse is mouse
    assert isinstance(bps.ports, Ports)
    assert bps.ports is bps.ports
    assert bps.ports.p1.kbm is kbm
    assert kbm.mapping is not None
    assert kbm.mapping.port is bps.ports.p1
    assert bps.ports.keyboard is keyboard
    assert bps.ports.mouse is mouse
    bps.ports.p1.disconnect(kbm)
    assert bps.ports.p1.keyboard is None
    assert bps.ports.p1.mouse is None
    bps.ports.p2.connect(kbm)
    assert bps.keyboard is keyboard
    assert bps.mouse is mouse
    glyphs = GlyphEngine(CharSet.ASCII8X12)
    console = Console(glyphs)
    console.pen[2] = OUTLINE
    console.background = 0

    game = Game(console)
    view = PromptView(console)
    console.foreground = NARRATION
    console.write(INTRO + "\n")
    command = keyboard.input().until(key.RETURN)

    events = bps.events
    script = [
        # a wrong verb: echo + rebuff
        (10, lambda: events.dispatch_event("on_text", "eat grue")),
        (15, lambda: events.dispatch_event("on_text", "\r")),
        # a typo fixed with backspace: "loop" -p +k submits as "look"
        (20, lambda: events.dispatch_event("on_text", "loop")),
        (25, lambda: events.dispatch_event(
            "on_text_motion", pyglet_key.MOTION_BACKSPACE)),
        (30, lambda: events.dispatch_event("on_text", "k")),
        (35, lambda: events.dispatch_event("on_text", "\r")),
    ]

    tick = 0
    step = 0
    transcript: list[str] = []

    def main_loop(engine) -> None:
        nonlocal command, tick, step
        if command.completed:
            line = command.value
            view.commit(line)
            game.respond(line)
            command = keyboard.input().until(key.RETURN)
        view.draw(command.value, tick)
        if step < len(script) and tick == script[step][0]:
            script[step][1]()
            step += 1
        elif step == len(script) and tick == script[-1][0] + 10:
            # a few ticks after the last submit: state has settled
            transcript.extend(grid_lines(console))
            events.dispatch_event("on_close")
            step += 1  # don't re-enter
        elif tick >= TICK_CAP:
            transcript.extend(grid_lines(console))
            events.dispatch_event("on_close")
        tick += 1

    bps.layers.add(glyphs)
    bps.run(main_loop)

    assert tick < TICK_CAP, "session hit the tick cap; script never completed"
    text = "\n".join(transcript)

    # echo: both submitted lines appear at prompts, the typo'd one corrected
    assert "> eat grue" in text
    assert "> look" in text
    assert "loop" not in text  # backspace erased the p before submit

    # responses: wrong verb rebuffed, look pays off
    assert "one-verb kind of night" in text
    assert "shine job in Butcher Bay" in text
    assert '"eep."' in text
    assert "The grue is likely to be punched by YOU." in text

    # a fresh prompt is waiting at the end of the transcript
    assert transcript and [l for l in transcript if l][-1].startswith(">")


if __name__ == "__main__":
    test_stream_input_session()
    print("ok")
