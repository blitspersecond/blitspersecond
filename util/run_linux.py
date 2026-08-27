#!/usr/bin/env python3
"""Launch a BPS Python program through the preferred Linux presentation path."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
from typing import Callable, Mapping


REPO_DIR = Path(__file__).resolve().parent.parent
DEFAULT_WIDTH = 2560
DEFAULT_HEIGHT = 1440
DEFAULT_REFRESH = 60
INTERNAL_WIDTH = 640
INTERNAL_HEIGHT = 360


@dataclass(frozen=True)
class LaunchPlan:
    command: list[str]
    environment: dict[str, str]
    presentation: str


def _positive_integer(environment: Mapping[str, str], name: str, default: int) -> int:
    raw = environment.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


def _environment_flag(
    environment: Mapping[str, str],
    name: str,
    default: bool = False,
) -> bool:
    raw = environment.get(name)
    if raw is None or not raw:
        return default
    if raw.casefold() in {"1", "true", "yes", "on"}:
        return True
    if raw.casefold() in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean flag")


def _refresh_from_xrandr(output: str) -> int | None:
    """Return the primary output's current integer refresh, if advertised."""
    current_output_is_primary = False
    fallback: float | None = None
    for line in output.splitlines():
        if line and not line[0].isspace():
            words = line.split()
            current_output_is_primary = (
                len(words) >= 2
                and words[1] == "connected"
                and "primary" in words[2:]
            )
            continue
        for token in line.split()[1:]:
            if "*" not in token:
                continue
            match = re.match(r"([0-9]+(?:\.[0-9]+)?)", token)
            if match is None:
                continue
            rate = float(match.group(1))
            if current_output_is_primary:
                return round(rate)
            if fallback is None:
                fallback = rate
    return round(fallback) if fallback is not None else None


def _size_from_xrandr(output: str) -> tuple[int, int] | None:
    """Return the primary output's active pixel dimensions, if advertised."""
    fallback: tuple[int, int] | None = None
    for line in output.splitlines():
        if not line or line[0].isspace():
            continue
        words = line.split()
        if len(words) < 2 or words[1] != "connected":
            continue
        geometry = re.search(r"([0-9]+)x([0-9]+)[+-][0-9]+[+-][0-9]+", line)
        if geometry is None:
            continue
        size = (int(geometry.group(1)), int(geometry.group(2)))
        if "primary" in words[2:]:
            return size
        if fallback is None:
            fallback = size
    return fallback


def _current_size(
    environment: Mapping[str, str],
    *,
    which: Callable[[str], str | None] = shutil.which,
) -> tuple[int, int]:
    xrandr = which("xrandr")
    if xrandr and environment.get("DISPLAY"):
        completed = subprocess.run(
            [xrandr, "--current"],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode == 0:
            detected = _size_from_xrandr(completed.stdout)
            if detected is not None:
                return detected
    return DEFAULT_WIDTH, DEFAULT_HEIGHT


def _current_refresh(
    environment: Mapping[str, str],
    *,
    which: Callable[[str], str | None] = shutil.which,
) -> int:
    explicit = environment.get("BPS_GAMESCOPE_REFRESH")
    if explicit is not None:
        return _positive_integer(
            environment,
            "BPS_GAMESCOPE_REFRESH",
            DEFAULT_REFRESH,
        )
    xrandr = which("xrandr")
    if xrandr and environment.get("DISPLAY"):
        completed = subprocess.run(
            [xrandr, "--current"],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode == 0:
            detected = _refresh_from_xrandr(completed.stdout)
            if detected is not None:
                return detected
    return DEFAULT_REFRESH


def _python_command(
    arguments: list[str],
    environment: Mapping[str, str],
    repo_dir: Path,
) -> list[str]:
    python = Path(
        environment.get("BPS_PYTHON", str(repo_dir / "venv" / "bin" / "python"))
    ).expanduser()
    if not python.is_file() or not os.access(python, os.X_OK):
        raise ValueError(
            f"BPS Python is not executable: {python} "
            "(set BPS_PYTHON to override it)"
        )

    arguments = list(arguments)
    if arguments and not arguments[0].startswith("-"):
        candidate = Path(arguments[0]).expanduser()
        if not candidate.exists():
            candidate = repo_dir / candidate
        if candidate.exists():
            arguments[0] = str(candidate.resolve())
    # Preserve the virtualenv entry-point path. Resolving its symlink to the
    # base interpreter would make Python lose the venv's pyvenv.cfg boundary.
    return [str(python.absolute()), *arguments]


def build_launch_plan(
    arguments: list[str],
    *,
    direct: bool,
    fullscreen: bool,
    mangohud: bool,
    environment: Mapping[str, str] | None = None,
    repo_dir: Path = REPO_DIR,
    which: Callable[[str], str | None] = shutil.which,
    refresh: int | None = None,
    display_size: tuple[int, int] | None = None,
) -> LaunchPlan:
    environment = dict(os.environ if environment is None else environment)
    fullscreen = fullscreen or _environment_flag(environment, "BPS_FULLSCREEN")
    python_command = _python_command(arguments, environment, repo_dir)
    environment["PYTHONPATH"] = os.pathsep.join(
        part
        for part in (
            str(repo_dir),
            environment.get("PYTHONPATH", ""),
        )
        if part
    )
    if fullscreen:
        environment["BPS_FULLSCREEN"] = "1"
    else:
        environment.pop("BPS_FULLSCREEN", None)

    gamescope = which("gamescope")
    inside_gamescope = bool(environment.get("GAMESCOPE_WAYLAND_DISPLAY"))
    use_gamescope = bool(gamescope) and not direct and not inside_gamescope

    if use_gamescope:
        default_width, default_height = (
            (
                _current_size(environment, which=which)
                if display_size is None
                else display_size
            )
            if fullscreen
            else (DEFAULT_WIDTH, DEFAULT_HEIGHT)
        )
        if min(default_width, default_height) < 1:
            raise ValueError("detected display dimensions must be positive")
        width = _positive_integer(
            environment,
            "BPS_GAMESCOPE_WIDTH",
            default_width,
        )
        height = _positive_integer(
            environment,
            "BPS_GAMESCOPE_HEIGHT",
            default_height,
        )
        selected_refresh = (
            _current_refresh(environment, which=which)
            if refresh is None
            else refresh
        )
        backend = environment.get(
            "BPS_GAMESCOPE_BACKEND",
            "wayland" if environment.get("WAYLAND_DISPLAY") else "auto",
        )
        environment["BPS_GAMESCOPE_BACKEND"] = backend
        gamescope_command = [
            str(gamescope),
            "--backend",
            backend,
            "-w",
            str(width),
            "-h",
            str(height),
            "-W",
            str(width),
            "-H",
            str(height),
            "-S",
            "integer",
            "-F",
            "nearest",
            "-r",
            str(selected_refresh),
        ]
        # Windowed is deliberately decorated. Gamescope's -b removes the
        # outer close/title controls; -f is reserved for explicit fullscreen.
        if fullscreen:
            gamescope_command.append("-f")
        if mangohud:
            mangoapp = which("mangoapp")
            if mangoapp is None:
                raise ValueError(
                    "--mangohud requested, but Gamescope's mangoapp is not on PATH"
                )
            gamescope_command.append("--mangoapp")
        command = [*gamescope_command, "--", *python_command]
        mode = "fullscreen" if fullscreen else "decorated window"
        overlay = " + MangoHud" if mangohud else ""
        integer_scale = min(width // INTERNAL_WIDTH, height // INTERNAL_HEIGHT)
        scale_description = (
            f", BPS {integer_scale}x integer"
            if integer_scale >= 1
            else ""
        )
        presentation = (
            f"Gamescope/{backend}, {mode}, "
            f"{width}x{height}@{selected_refresh}Hz"
            f"{scale_description}{overlay}"
        )
        return LaunchPlan(command, environment, presentation)

    environment.pop("BPS_GAMESCOPE_BACKEND", None)
    command = python_command
    if mangohud:
        executable = which("mangohud")
        if executable is None:
            raise ValueError("--mangohud requested, but mangohud is not on PATH")
        command = [executable, *command]
    reason = (
        "already inside Gamescope"
        if inside_gamescope
        else "explicit --direct"
        if direct
        else "Gamescope unavailable"
    )
    overlay = " + MangoHud" if mangohud else ""
    return LaunchPlan(command, environment, f"direct Linux ({reason}){overlay}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run a BPS Python program using Gamescope when available. "
            "Use -- before Python options such as -m or -c."
        )
    )
    parser.add_argument(
        "--direct",
        action="store_true",
        help="bypass Gamescope even when it is available",
    )
    parser.add_argument(
        "--fullscreen",
        action="store_true",
        help="fullscreen both Gamescope and the BPS surface",
    )
    parser.add_argument(
        "--mangohud",
        action="store_true",
        help="enable MangoHud (Gamescope mangoapp when wrapped)",
    )
    parser.add_argument("python_arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    python_arguments = args.python_arguments
    if python_arguments[:1] == ["--"]:
        python_arguments = python_arguments[1:]
    if not python_arguments:
        parser.error("a Python script, -m module, or -c program is required")

    try:
        plan = build_launch_plan(
            python_arguments,
            direct=args.direct,
            fullscreen=args.fullscreen,
            mangohud=args.mangohud,
        )
    except ValueError as error:
        parser.error(str(error))

    print(f"Presentation: {plan.presentation}")
    print(f"Command: {shlex.join(plan.command)}")
    sys.stdout.flush()
    os.chdir(REPO_DIR)
    os.execvpe(plan.command[0], plan.command, plan.environment)
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
