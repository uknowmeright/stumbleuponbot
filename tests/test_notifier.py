"""Tests for the notifier module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from stumbleupon import notifier


# ---------------------------------------------------------------------------
# is_macos
# ---------------------------------------------------------------------------


def test_is_macos_true_on_darwin(monkeypatch: pytest.MonkeyPatch) -> None:
    """is_macos returns True when sys.platform == 'darwin'."""
    monkeypatch.setattr(notifier.sys, "platform", "darwin")
    assert notifier.is_macos() is True


def test_is_macos_false_on_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    """is_macos returns False on non-darwin platforms."""
    monkeypatch.setattr(notifier.sys, "platform", "linux")
    assert notifier.is_macos() is False


# ---------------------------------------------------------------------------
# notify
# ---------------------------------------------------------------------------


def test_notify_calls_osascript_on_macos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On macOS, notify invokes osascript with display-notification."""
    monkeypatch.setattr(notifier.sys, "platform", "darwin")

    called: dict = {}

    def fake_run(argv, **kwargs):
        called["argv"] = argv
        called["kwargs"] = kwargs
        return MagicMock(returncode=0)

    monkeypatch.setattr(notifier.subprocess, "run", fake_run)

    notifier.notify("Title", "Body text", sound="Basso")

    argv = called["argv"]
    assert argv[0] == "osascript"
    assert argv[1] == "-e"
    script = argv[2]
    assert "Title" in script
    assert "Body text" in script
    assert "Basso" in script


def test_notify_is_noop_on_non_macos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On Linux, notify returns without calling subprocess at all."""
    monkeypatch.setattr(notifier.sys, "platform", "linux")

    def fake_run(*args, **kwargs):
        raise AssertionError("subprocess.run should not be called on non-macOS")

    monkeypatch.setattr(notifier.subprocess, "run", fake_run)
    notifier.notify("Title", "Body")  # must not raise


def test_notify_does_not_raise_on_osascript_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If osascript fails, notify logs and returns — never raises."""
    import subprocess as sp

    monkeypatch.setattr(notifier.sys, "platform", "darwin")

    def fake_run(*args, **kwargs):
        raise sp.CalledProcessError(1, ["osascript"], stderr=b"permission denied")

    monkeypatch.setattr(notifier.subprocess, "run", fake_run)
    notifier.notify("Title", "Body")  # must not raise
