import os
from pathlib import Path

from util import run_linux


def _repo(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    python = repo / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    python.chmod(0o755)
    script = repo / "game.py"
    script.write_text("", encoding="utf-8")
    return repo, script


def _which(*names: str):
    paths = {name: f"/usr/bin/{name}" for name in names}
    return paths.get


def test_gamescope_is_default_and_windowed_mode_keeps_decorations(tmp_path):
    repo, script = _repo(tmp_path)

    plan = run_linux.build_launch_plan(
        ["game.py", "--level", "1"],
        direct=False,
        fullscreen=False,
        mangohud=False,
        environment={
            "DISPLAY": ":0",
            "WAYLAND_DISPLAY": "wayland-0",
        },
        repo_dir=repo,
        which=_which("gamescope"),
        refresh=120,
    )

    assert plan.command[:3] == [
        "/usr/bin/gamescope",
        "--backend",
        "wayland",
    ]
    assert "-b" not in plan.command
    assert "-f" not in plan.command
    assert plan.command[-3:] == [str(script), "--level", "1"]
    assert plan.environment["BPS_GAMESCOPE_BACKEND"] == "wayland"
    assert "BPS_FULLSCREEN" not in plan.environment
    assert "decorated window" in plan.presentation


def test_fullscreen_and_mangohud_are_applied_at_gamescope_boundary(tmp_path):
    repo, _script = _repo(tmp_path)

    plan = run_linux.build_launch_plan(
        ["game.py"],
        direct=False,
        fullscreen=True,
        mangohud=True,
        environment={"WAYLAND_DISPLAY": "wayland-0"},
        repo_dir=repo,
        which=_which("gamescope", "mangoapp"),
        refresh=120,
        display_size=(3840, 2160),
    )

    assert "-f" in plan.command
    assert "--mangoapp" in plan.command
    assert plan.command[plan.command.index("-w") + 1] == "3840"
    assert plan.command[plan.command.index("-h") + 1] == "2160"
    assert plan.command[plan.command.index("-W") + 1] == "3840"
    assert plan.command[plan.command.index("-H") + 1] == "2160"
    assert plan.command[plan.command.index("-S") + 1] == "integer"
    assert plan.command[plan.command.index("-F") + 1] == "nearest"
    assert plan.environment["BPS_FULLSCREEN"] == "1"
    assert "BPS 6x integer" in plan.presentation


def test_fullscreen_environment_selects_both_outer_and_inner_modes(tmp_path):
    repo, _script = _repo(tmp_path)

    plan = run_linux.build_launch_plan(
        ["game.py"],
        direct=False,
        fullscreen=False,
        mangohud=False,
        environment={
            "BPS_FULLSCREEN": "yes",
            "WAYLAND_DISPLAY": "wayland-0",
        },
        repo_dir=repo,
        which=_which("gamescope"),
        refresh=120,
        display_size=(3840, 2160),
    )

    assert "-f" in plan.command
    assert plan.environment["BPS_FULLSCREEN"] == "1"


def test_direct_mangohud_wraps_the_venv_python(tmp_path):
    repo, script = _repo(tmp_path)

    plan = run_linux.build_launch_plan(
        ["game.py"],
        direct=True,
        fullscreen=False,
        mangohud=True,
        environment={},
        repo_dir=repo,
        which=_which("gamescope", "mangohud"),
    )

    assert plan.command == [
        "/usr/bin/mangohud",
        str(repo / "venv/bin/python"),
        str(script),
    ]
    assert "explicit --direct" in plan.presentation


def test_refresh_parser_prefers_primary_current_mode():
    output = """
Screen 0: minimum 16 x 16, current 5760 x 2160, maximum 32767 x 32767
DP-1 connected 1920x1080+0+0
   1920x1080     60.00*+
DP-2 connected primary 3840x2160+1920+0
   3840x2160    119.98*+
"""

    assert run_linux._refresh_from_xrandr(output) == 120


def test_size_parser_prefers_primary_active_mode():
    output = """
Screen 0: minimum 16 x 16, current 5760 x 2160, maximum 32767 x 32767
DP-1 connected 1920x1080+0+0
   1920x1080     60.00*+
DP-2 connected primary 3840x2160+1920+0
   3840x2160    119.98*+
"""

    assert run_linux._size_from_xrandr(output) == (3840, 2160)


def test_windowed_size_remains_configured_instead_of_using_display(tmp_path):
    repo, _script = _repo(tmp_path)

    plan = run_linux.build_launch_plan(
        ["game.py"],
        direct=False,
        fullscreen=False,
        mangohud=False,
        environment={"WAYLAND_DISPLAY": "wayland-0"},
        repo_dir=repo,
        which=_which("gamescope"),
        refresh=120,
        display_size=(3840, 2160),
    )

    assert plan.command[plan.command.index("-w") + 1] == "2560"
    assert plan.command[plan.command.index("-h") + 1] == "1440"
    assert "BPS 4x integer" in plan.presentation


def test_pythonpath_keeps_repo_first_without_losing_existing_value(tmp_path):
    repo, _script = _repo(tmp_path)

    plan = run_linux.build_launch_plan(
        ["game.py"],
        direct=True,
        fullscreen=False,
        mangohud=False,
        environment={"PYTHONPATH": "/somewhere/else"},
        repo_dir=repo,
        which=_which(),
    )

    assert plan.environment["PYTHONPATH"] == os.pathsep.join(
        [str(repo), "/somewhere/else"]
    )
