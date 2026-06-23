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


# ---------------------------------------------------------------------------
# AppleScript escaping (regression for unescaped-quote crash)
# ---------------------------------------------------------------------------


def test_notify_escapes_double_quotes_in_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A title containing `"` must not close the AppleScript string literal
    prematurely and inject the rest of the title as a bare token."""
    monkeypatch.setattr(notifier.sys, "platform", "darwin")

    called: dict = {}

    def fake_run(argv, **kwargs):
        called["script"] = argv[2]
        return MagicMock(returncode=0)

    monkeypatch.setattr(notifier.subprocess, "run", fake_run)

    notifier.notify('He said "hi"', "body text")

    script = called["script"]
    # The unescaped quote must NOT appear next to the closing of the
    # title literal — that would mean AppleScript sees two adjacent
    # string literals and parses "hi" as a bare token.
    assert 'title "He said \\"hi\\""' in script


def test_notify_escapes_double_quotes_in_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(notifier.sys, "platform", "darwin")

    called: dict = {}

    def fake_run(argv, **kwargs):
        called["script"] = argv[2]
        return MagicMock(returncode=0)

    monkeypatch.setattr(notifier.subprocess, "run", fake_run)

    notifier.notify("Title", 'Status: "OK"')

    script = called["script"]
    assert 'notification "Status: \\"OK\\""' in script


def test_notify_escapes_backslashes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Backslashes must be doubled so a literal backslash survives AppleScript parsing."""
    monkeypatch.setattr(notifier.sys, "platform", "darwin")

    called: dict = {}

    def fake_run(argv, **kwargs):
        called["script"] = argv[2]
        return MagicMock(returncode=0)

    monkeypatch.setattr(notifier.subprocess, "run", fake_run)

    notifier.notify(r"Path: C:\Users\you", "body")

    script = called["script"]
    # AppleScript will collapse \\ -> \, so the actual string on the
    # receiving end is the original "Path: C:\Users\you".
    assert 'title "Path: C:\\\\Users\\\\you"' in script


def test_notify_escaping_produces_well_formed_applescript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A title with both quotes and backslashes should still produce a
    single, parseable AppleScript literal — no early terminator."""
    monkeypatch.setattr(notifier.sys, "platform", "darwin")

    called: dict = {}

    def fake_run(argv, **kwargs):
        called["script"] = argv[2]
        return MagicMock(returncode=0)

    monkeypatch.setattr(notifier.subprocess, "run", fake_run)

    # Tricky string: a backslash followed by a quote. After escaping it
    # becomes `\\\"` (3 chars: backslash, backslash, backslash, quote).
    notifier.notify(r'she said "hi" \o/', "body")

    script = called["script"]
    # No premature terminator: the substring after the title literal
    # should be the next AppleScript keyword, not raw input text.
    assert 'with title "she said \\"hi\\" \\\\o/"' in script


def test_escape_applescript_string_unit() -> None:
    """The escape helper is the unit; verify it in isolation."""
    assert notifier._escape_applescript_string("plain") == "plain"
    assert notifier._escape_applescript_string('a"b') == 'a\\"b'
    assert notifier._escape_applescript_string("a\\b") == "a\\\\b"
    assert notifier._escape_applescript_string(r'a\"b') == 'a\\\\\\"b'
