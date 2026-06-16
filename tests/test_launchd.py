"""Tests for the launchd module."""

from __future__ import annotations

import plistlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from stumbleupon import launchd


# ---------------------------------------------------------------------------
# is_macos / default_* helpers
# ---------------------------------------------------------------------------


def test_is_macos_true_on_darwin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(launchd.sys, "platform", "darwin")
    assert launchd.is_macos() is True


def test_is_macos_false_on_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(launchd.sys, "platform", "linux")
    assert launchd.is_macos() is False


def test_default_python_path_returns_sys_executable() -> None:
    import sys
    assert launchd.default_python_path() == sys.executable


def test_default_project_root_returns_cwd() -> None:
    import os
    assert launchd.default_project_root() == Path(os.getcwd())


def test_default_log_dir_is_data_logs_under_root(tmp_path: Path) -> None:
    """default_log_dir is <project_root>/data/logs."""
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(launchd, "default_project_root", lambda: tmp_path)
    assert launchd.default_log_dir() == tmp_path / "data" / "logs"
    monkeypatch.undo()


# ---------------------------------------------------------------------------
# render_plist + merge_*_schedule
# ---------------------------------------------------------------------------


def test_render_plist_roundtrips_via_plistlib(tmp_path: Path) -> None:
    """render_plist returns valid binary plist that round-trips through loads."""
    label = "com.example.test"
    args = ["/usr/bin/python", "-m", "stumbleupon", "run"]
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    out = launchd.render_plist(label, args, working_dir=tmp_path, log_dir=log_dir)
    assert isinstance(out, bytes)
    parsed = plistlib.loads(out)
    assert parsed["Label"] == label
    assert parsed["ProgramArguments"] == args
    assert parsed["WorkingDirectory"] == str(tmp_path)
    assert "out.log" in parsed["StandardOutPath"]
    assert "err.log" in parsed["StandardErrorPath"]
    assert parsed["RunAtLoad"] is False


def test_merge_calendar_schedule_adds_single_entry() -> None:
    """merge_calendar_schedule adds one StartCalendarInterval entry."""
    plist: dict = {"Label": "x"}
    launchd.merge_calendar_schedule(plist, hour=10, minute=30)
    assert plist["StartCalendarInterval"] == [{"Hour": 10, "Minute": 30}]


def test_merge_calendar_schedule_multi_adds_multiple_entries() -> None:
    """The pipeline plist runs at 10am AND 8pm — multi-entry helper needed."""
    plist: dict = {"Label": "x"}
    launchd.merge_calendar_schedule_multi(
        plist, [(10, 0), (20, 0)],
    )
    assert plist["StartCalendarInterval"] == [
        {"Hour": 10, "Minute": 0},
        {"Hour": 20, "Minute": 0},
    ]


def test_merge_interval_schedule_sets_start_interval() -> None:
    """The poster plist uses StartInterval (every 15 min)."""
    plist: dict = {"Label": "x"}
    launchd.merge_interval_schedule(plist, seconds=900)
    assert plist["StartInterval"] == 900


def test_installed_plist_path_uses_launch_agents_dir(tmp_path: Path) -> None:
    """installed_plist_path returns ~/Library/LaunchAgents/<label>.plist."""
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    path = launchd.installed_plist_path("com.example.foo")
    assert path == tmp_path / "Library" / "LaunchAgents" / "com.example.foo.plist"
    monkeypatch.undo()


# ---------------------------------------------------------------------------
# install_plist / uninstall_plist
# ---------------------------------------------------------------------------


def test_install_plist_copies_to_launch_agents_and_loads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """install_plist copies the plist to LaunchAgents and runs launchctl load."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / "Library" / "LaunchAgents").mkdir(parents=True)

    called: dict = {}

    def fake_run(argv, **kwargs):
        called["argv"] = argv
        return MagicMock(returncode=0)

    monkeypatch.setattr(launchd.subprocess, "run", fake_run)

    plist_bytes = b"FAKE PLIST"
    dest = launchd.install_plist("com.example.foo", plist_bytes)

    assert dest == tmp_path / "Library" / "LaunchAgents" / "com.example.foo.plist"
    assert dest.read_bytes() == b"FAKE PLIST"
    argv = called["argv"]
    assert argv[0] == "launchctl"
    assert "load" in argv
    assert str(dest) in argv


def test_install_plist_with_run_loadctl_false_skips_launchctl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If run_loadctl=False, the file is copied but launchctl is not invoked."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / "Library" / "LaunchAgents").mkdir(parents=True)

    def fake_run(*args, **kwargs):
        raise AssertionError("subprocess.run should not be called")

    monkeypatch.setattr(launchd.subprocess, "run", fake_run)

    dest = launchd.install_plist("com.example.foo", b"X", run_loadctl=False)
    assert dest.exists()


def test_uninstall_plist_unloads_and_deletes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """uninstall_plist runs launchctl unload then deletes the plist file."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    agents_dir = tmp_path / "Library" / "LaunchAgents"
    agents_dir.mkdir(parents=True)
    plist = agents_dir / "com.example.foo.plist"
    plist.write_bytes(b"X")

    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return MagicMock(returncode=0)

    monkeypatch.setattr(launchd.subprocess, "run", fake_run)

    removed = launchd.uninstall_plist("com.example.foo")

    assert removed is True
    assert not plist.exists()
    # The first call should be `launchctl unload`, the path argument matches.
    unload_argv = calls[0]
    assert unload_argv[0] == "launchctl"
    assert "unload" in unload_argv


def test_uninstall_plist_returns_false_if_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the plist file doesn't exist, return False and don't call launchctl."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / "Library" / "LaunchAgents").mkdir(parents=True)

    def fake_run(*args, **kwargs):
        raise AssertionError("launchctl should not be called if plist absent")

    monkeypatch.setattr(launchd.subprocess, "run", fake_run)
    assert launchd.uninstall_plist("com.example.foo") is False


# ---------------------------------------------------------------------------
# install_all / uninstall_all
# ---------------------------------------------------------------------------


def test_install_all_returns_three_labels_on_macos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On macOS, install_all renders and installs the pipeline + sounds + poster plists."""
    monkeypatch.setattr(launchd.sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / "Library" / "LaunchAgents").mkdir(parents=True)
    monkeypatch.setattr(launchd.subprocess, "run", lambda *a, **kw: MagicMock(returncode=0))

    result = launchd.install_all()
    assert set(result.keys()) == set(launchd.ALL_LABELS)
    for label, path in result.items():
        assert path.exists()
        # The file is a valid plist
        parsed = plistlib.loads(path.read_bytes())
        assert parsed["Label"] == label


def test_install_all_is_noop_on_non_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    """On Linux, install_all returns {} and doesn't touch the filesystem."""
    monkeypatch.setattr(launchd.sys, "platform", "linux")

    def fake_run(*args, **kwargs):
        raise AssertionError("launchctl should not be called on non-macOS")

    monkeypatch.setattr(launchd.subprocess, "run", fake_run)
    assert launchd.install_all() == {}


def test_uninstall_all_is_symmetric(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """uninstall_all removes all 3 plists; each is reported as removed."""
    monkeypatch.setattr(launchd.sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    agents_dir = tmp_path / "Library" / "LaunchAgents"
    agents_dir.mkdir(parents=True)
    for label in launchd.ALL_LABELS:
        (agents_dir / f"{label}.plist").write_bytes(b"X")
    monkeypatch.setattr(
        launchd.subprocess, "run", lambda *a, **kw: MagicMock(returncode=0),
    )

    result = launchd.uninstall_all()
    assert result == {label: True for label in launchd.ALL_LABELS}
    assert not any((agents_dir / f"{label}.plist").exists() for label in launchd.ALL_LABELS)


def test_uninstall_all_is_noop_on_non_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    """On Linux, uninstall_all returns {} without touching the filesystem."""
    monkeypatch.setattr(launchd.sys, "platform", "linux")

    def fake_run(*args, **kwargs):
        raise AssertionError("launchctl should not be called on non-macOS")

    monkeypatch.setattr(launchd.subprocess, "run", fake_run)
    assert launchd.uninstall_all() == {}


# ---------------------------------------------------------------------------
# _build_*_plist schedule tests (lock in the actual schedule values)
# ---------------------------------------------------------------------------


def test_build_pipeline_plist_runs_at_10am_and_8pm(tmp_path: Path) -> None:
    """The pipeline plist must run at 10am and 8pm (2x/day)."""
    plist_bytes = launchd._build_pipeline_plist(
        python="/usr/bin/python", project=tmp_path, log_dir=tmp_path / "logs",
    )
    parsed = plistlib.loads(plist_bytes)
    assert parsed["StartCalendarInterval"] == [
        {"Hour": 10, "Minute": 0},
        {"Hour": 20, "Minute": 0},
    ]


def test_build_sounds_plist_runs_daily_at_3am(tmp_path: Path) -> None:
    """The sounds plist must run daily at 3am (one entry in the calendar list)."""
    plist_bytes = launchd._build_sounds_plist(
        python="/usr/bin/python", project=tmp_path, log_dir=tmp_path / "logs",
    )
    parsed = plistlib.loads(plist_bytes)
    # The implementation always wraps calendar entries in a list, even
    # for single-entry schedules — this is consistent with the
    # multi-entry case (pipeline plist) and is valid launchd syntax.
    assert parsed["StartCalendarInterval"] == [{"Hour": 3, "Minute": 0}]


def test_build_poster_plist_runs_every_15_minutes(tmp_path: Path) -> None:
    """The poster plist must use StartInterval=900 (15 minutes), not calendar entries."""
    plist_bytes = launchd._build_poster_plist(
        python="/usr/bin/python", project=tmp_path, log_dir=tmp_path / "logs",
    )
    parsed = plistlib.loads(plist_bytes)
    assert parsed["StartInterval"] == 900
    assert "StartCalendarInterval" not in parsed
