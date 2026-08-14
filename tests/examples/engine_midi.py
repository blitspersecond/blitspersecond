"""Perform a MIDI score through the Stage audio graph behind its cover art.

The recording presentation defaults to fullscreen 4K (6x), fades the cover
in as the music begins, then finishes a ten-second fade to black one second
after the audio ends and exits. Close the window to stop it early.

    python tests/examples/engine_midi.py song.mid image.png [options]
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from blitspersecond import BlitsPerSecond
from blitspersecond.audio.common import SAMPLE_RATE
from blitspersecond.graphics import PixelBuffer, TileEngine
from blitspersecond.resources import ResourceManager
from blitspersecond.system import Config
from tests.examples.demo_midi import load_events, load_midi_file, schedule

def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("midi", help="Standard MIDI File to perform")
    parser.add_argument("image", help="cover image to present behind the score")
    parser.add_argument(
        "--lead-time",
        type=float,
        default=0.0,
        help="seconds of black/silence before playback (default: 0)",
    )
    parser.add_argument(
        "--fade-in",
        type=float,
        default=3.0,
        help="backdrop fade-in duration once playback starts (default: 3)",
    )
    parser.add_argument(
        "--fade-out",
        type=float,
        default=10.0,
        help="fade-to-black duration after playback (default: 10)",
    )
    parser.add_argument(
        "--scale",
        type=int,
        default=6,
        help="integer presentation scale (default: 6, giving 3840x2160)",
    )
    parser.add_argument(
        "--windowed",
        action="store_true",
        help="open a window instead of fullscreen",
    )
    parser.add_argument(
        "--no-grime",
        action="store_true",
        help="bypass the MIDI bus noise/hum bed for an A/B comparison",
    )
    parser.add_argument("--start-gate", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.lead_time < 0 or args.fade_in < 0 or args.fade_out < 0:
        parser.error("fade and lead times must be non-negative")
    if args.scale < 1:
        parser.error("scale must be at least 1")
    return args


def presentation_fade(
    now,
    lead_time,
    playback_end,
    fade_in,
    fade_out,
    fade_out_tail=1.0,
):
    """Return the recording envelope for a chip-clock timestamp."""
    if now < lead_time:
        return 0.0

    fade = 1.0
    if fade_in and now < lead_time + fade_in:
        fade = (now - lead_time) / fade_in

    fade_out_end = playback_end + fade_out_tail
    fade_out_start = fade_out_end - fade_out
    if now >= fade_out_end:
        return 0.0
    if fade_out and now >= fade_out_start:
        fade = min(fade, (fade_out_end - now) / fade_out)
    return fade


def main():
    args = parse_args()
    display = Config().display
    display.scale = args.scale
    display.fullscreen = not args.windowed

    bps = BlitsPerSecond()
    rm = ResourceManager()

    # The cover: the full untouched file as one whole-buffer tile on its
    # own layer (auto-added). The fade envelope drives its rgb registers
    # (toward 0 = to black); it starts black and fades in with the music.
    image = PixelBuffer(rm.image(args.image))
    cover = TileEngine(image, tilewidth=image.width, tileheight=image.height)
    cover.blit(0, 0, 0)
    cover.red = cover.green = cover.blue = 0.0

    # Offset the score itself so the lead-in is silent even though STARTING
    # opens the audio stream immediately.
    events, programs = load_events(load_midi_file(args.midi))
    playback_end = schedule(
        bps.audio,
        events,
        programs,
        offset=args.lead_time,
        grime=not args.no_grime,
    )
    fade_out_end = playback_end + 1.0
    fade_out_start = fade_out_end - args.fade_out
    stop_at = fade_out_end
    print(
        f"{Path(args.midi).name}: fade-out {fade_out_start:.1f}s–"
        f"{fade_out_end:.1f}s; audio ends at {playback_end:.1f}s"
    )

    if args.start_gate:
        # A recording wrapper can use this handshake to attach x11grab to the
        # XWayland window before STARTING opens audio and advances its clock.
        gate = Path(args.start_gate)
        gate.touch()
        print("Window ready; waiting for recorder...")
        while gate.exists():
            bps._display.dispatch_events()
            if bps._display.closed:
                return
            time.sleep(0.01)

    black_presented = False

    def main_loop(engine):
        nonlocal black_presented
        # The output device advances this sample clock. Audio startup is a
        # blocking engine readiness gate, so device failure aborts before the
        # first presentation rather than silently substituting wall time.
        now = engine.audio.clock / SAMPLE_RATE
        level = presentation_fade(
            now,
            args.lead_time,
            playback_end,
            args.fade_in,
            args.fade_out,
        )
        cover.red = cover.green = cover.blue = level
        if now >= stop_at:
            if black_presented:
                engine.stop()
            else:
                # Leave one flip between reaching black and closing the window
                # so the captured output contains a genuinely black frame.
                black_presented = True

    bps.run(main_loop)


if __name__ == "__main__":
    main()
