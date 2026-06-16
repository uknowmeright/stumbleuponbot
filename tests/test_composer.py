"""Tests for the composer module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from stumbleupon import composer


# ---------------------------------------------------------------------------
# build_ffmpeg_command
# ---------------------------------------------------------------------------


def test_build_ffmpeg_command_video_only_omits_audio_input() -> None:
    """No sound: just the video, audio dropped with -an flag."""
    cmd = composer.build_ffmpeg_command(
        recording_path=Path("data/recordings/1.webm"),
        output_path=Path("data/final/1.mp4"),
        sound_path=None,
    )
    # ffmpeg binary, -y overwrite, single input, duration, scale filter, libx264, -an, output
    assert cmd[0] == "ffmpeg"
    assert "-y" in cmd
    assert "data/recordings/1.webm" in cmd
    assert "data/final/1.mp4" in cmd
    assert "-t" in cmd
    assert "30.0" in cmd  # default duration
    # No -i for sound, no -map, no -af
    assert cmd.count("-i") == 1
    assert "-af" not in cmd
    assert "-an" in cmd  # audio disabled


def test_build_ffmpeg_command_with_sound_mixes_audio() -> None:
    """With sound: two inputs, mix in the sound at 70% volume."""
    cmd = composer.build_ffmpeg_command(
        recording_path=Path("data/recordings/1.webm"),
        output_path=Path("data/final/1.mp4"),
        sound_path=Path("data/sounds/viral.mp3"),
    )
    assert cmd.count("-i") == 2
    assert "data/recordings/1.webm" in cmd
    assert "data/sounds/viral.mp3" in cmd
    # Audio mixing: map video from input 0, audio from input 1, volume filter
    assert "-map" in cmd
    assert "0:v:0" in cmd
    assert "1:a:0" in cmd
    assert "-af" in cmd
    assert "volume=0.7" in cmd
    assert "-shortest" in cmd
    # No -an flag when sound is provided
    assert "-an" not in cmd


def test_build_ffmpeg_command_respects_duration() -> None:
    cmd = composer.build_ffmpeg_command(
        recording_path=Path("r.webm"),
        output_path=Path("o.mp4"),
        sound_path=None,
        duration_sec=15.0,
    )
    # -t 15.0
    t_idx = cmd.index("-t")
    assert cmd[t_idx + 1] == "15.0"


def test_build_ffmpeg_command_scales_to_tiktok_format() -> None:
    """Output is 1080x1920 with letterboxing to preserve aspect ratio."""
    cmd = composer.build_ffmpeg_command(
        recording_path=Path("r.webm"),
        output_path=Path("o.mp4"),
        sound_path=None,
    )
    # -vf "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:..."
    vf_idx = cmd.index("-vf")
    vf_value = cmd[vf_idx + 1]
    assert "scale=1080:1920" in vf_value
    assert "force_original_aspect_ratio=decrease" in vf_value
    assert "pad=1080:1920" in vf_value
    # Codec: libx264 video, aac audio (only when sound present)
    assert "libx264" in cmd


def test_build_ffmpeg_command_uses_h264_and_aac_codecs() -> None:
    cmd = composer.build_ffmpeg_command(
        recording_path=Path("r.webm"),
        output_path=Path("o.mp4"),
        sound_path=Path("s.mp3"),
    )
    # libx264 video, aac audio
    assert "libx264" in cmd
    assert "aac" in cmd


# ---------------------------------------------------------------------------
# resolve_output_path
# ---------------------------------------------------------------------------


def test_resolve_output_path_uses_clip_id() -> None:
    assert composer.resolve_output_path(Path("data/final"), 42) == Path("data/final/42.mp4")


# ---------------------------------------------------------------------------
# compose_clip
# ---------------------------------------------------------------------------


def test_compose_clip_runs_subprocess_with_built_command(tmp_path: Path) -> None:
    """The function should call subprocess.run with the args from build_ffmpeg_command."""
    from unittest.mock import MagicMock, patch

    rec = tmp_path / "1.webm"
    rec.write_bytes(b"fake webm")
    out = tmp_path / "1.mp4"
    sound = tmp_path / "s.mp3"
    sound.write_bytes(b"fake mp3")

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = b""
    mock_result.stderr = b""

    with patch("stumbleupon.composer.subprocess.run", return_value=mock_result) as mock_run:
        composer.compose_clip(
            recording_path=rec,
            output_path=out,
            sound_path=sound,
            duration_sec=30.0,
        )

    mock_run.assert_called_once()
    call_args = mock_run.call_args
    cmd = call_args.args[0]
    assert cmd[0] == "ffmpeg"
    assert str(rec) in cmd
    assert str(sound) in cmd
    assert str(out) in cmd
    assert call_args.kwargs.get("check") is True


def test_compose_clip_creates_output_dir_if_missing(tmp_path: Path) -> None:
    """The function should mkdir -p the output dir before running ffmpeg."""
    from unittest.mock import MagicMock, patch

    rec = tmp_path / "1.webm"
    rec.write_bytes(b"fake")
    out = tmp_path / "subdir" / "deep" / "1.mp4"  # nested path that doesn't exist

    mock_result = MagicMock()
    with patch("stumbleupon.composer.subprocess.run", return_value=mock_result):
        composer.compose_clip(
            recording_path=rec, output_path=out, sound_path=None, duration_sec=30.0,
        )

    assert out.parent.exists()


# ---------------------------------------------------------------------------
# compose_pending_clips (orchestrator)
# ---------------------------------------------------------------------------


def test_compose_pending_clips_calls_compose_clip_per_clip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end with mocked compose_clip. Verifies each clip is processed."""
    import sqlite3
    from stumbleupon.db import init_db
    from stumbleupon import composer, queue

    db_path = tmp_path / "stumbleupon.db"
    init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        site_id = conn.execute("INSERT INTO sites (url) VALUES ('https://x.com')").lastrowid
        clip_id = conn.execute(
            "INSERT INTO clips (site_id, status, recording_path) "
            "VALUES (?, 'pending', ?)",
            (site_id, str(tmp_path / "1.webm")),
        ).lastrowid
        conn.commit()

    finals_dir = tmp_path / "final"
    seen_args: list[tuple] = []

    def fake_compose_clip(recording_path, output_path, sound_path=None, duration_sec=30.0):
        seen_args.append((recording_path, output_path, sound_path, duration_sec))
        # Simulate ffmpeg writing the file
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"fake mp4")

    monkeypatch.setattr(composer, "compose_clip", fake_compose_clip)

    results = composer.compose_pending_clips(
        db_path=db_path, finals_dir=finals_dir, limit=5,
    )

    assert len(results) == 1
    assert results[0]["clip_id"] == clip_id
    assert len(seen_args) == 1
    rec, out, snd, dur = seen_args[0]
    assert str(rec) == str(tmp_path / "1.webm")
    assert str(out) == str(finals_dir / f"{clip_id}.mp4")
    assert snd is None
    assert dur == 30.0

    # Verify the DB was updated
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT status, final_path FROM clips WHERE id=?", (clip_id,)
        ).fetchone()
    assert row["status"] == "pending"
    assert row["final_path"] == str(finals_dir / f"{clip_id}.mp4")


def test_compose_pending_clips_handles_per_clip_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One bad clip doesn't sink the batch."""
    import sqlite3
    from stumbleupon.db import init_db
    from stumbleupon import composer, queue

    db_path = tmp_path / "stumbleupon.db"
    init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        site_id = conn.execute("INSERT INTO sites (url) VALUES ('https://x.com')").lastrowid
        a = conn.execute(
            "INSERT INTO clips (site_id, status, recording_path) "
            "VALUES (?, 'pending', ?)",
            (site_id, str(tmp_path / "1.webm")),
        ).lastrowid
        b = conn.execute(
            "INSERT INTO clips (site_id, status, recording_path) "
            "VALUES (?, 'pending', ?)",
            (site_id, str(tmp_path / "2.webm")),
        ).lastrowid
        conn.commit()

    finals_dir = tmp_path / "final"

    def fake_compose_clip(recording_path, output_path, sound_path=None, duration_sec=30.0):
        if "2.webm" in str(recording_path):
            raise RuntimeError("ffmpeg crashed")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"fake mp4")

    monkeypatch.setattr(composer, "compose_clip", fake_compose_clip)

    results = composer.compose_pending_clips(
        db_path=db_path, finals_dir=finals_dir, limit=5,
    )

    # 'a' succeeded; 'b' was marked failed (via mark_site_failed on the parent site)
    assert len(results) == 1
    assert results[0]["clip_id"] == a

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = {
            r["url"]: (r["status"], r["skip_reason"])
            for r in conn.execute("SELECT url, status, skip_reason FROM sites").fetchall()
        }
    assert rows["https://x.com"][0] == "failed"
    assert "ffmpeg crashed" in rows["https://x.com"][1]


def test_compose_pending_clips_passes_per_clip_sound_path_from_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When the caller does not override `sound_path`, each clip's joined
    `sounds.audio_path` is forwarded to `compose_clip`.

    This is the fix for the integration bug where the composer always
    produced silent mp4s because it never read `clips.sound_id`.
    """
    import sqlite3
    from stumbleupon.db import init_db
    from stumbleupon import composer

    db_path = tmp_path / "stumbleupon.db"
    init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        site_id = conn.execute("INSERT INTO sites (url) VALUES ('https://x.com')").lastrowid
        s1 = conn.execute(
            "INSERT INTO sounds (tiktok_sound_id, audio_path) VALUES ('s1', '/tmp/s1.mp3')"
        ).lastrowid
        s2 = conn.execute(
            "INSERT INTO sounds (tiktok_sound_id, audio_path) VALUES ('s2', '/tmp/s2.mp3')"
        ).lastrowid
        # Clip with sound s1
        a = conn.execute(
            "INSERT INTO clips (site_id, status, recording_path, sound_id) "
            "VALUES (?, 'pending', ?, ?)",
            (site_id, str(tmp_path / "1.webm"), s1),
        ).lastrowid
        # Clip with sound s2
        b = conn.execute(
            "INSERT INTO clips (site_id, status, recording_path, sound_id) "
            "VALUES (?, 'pending', ?, ?)",
            (site_id, str(tmp_path / "2.webm"), s2),
        ).lastrowid
        # Clip with no sound attached — should be composed with sound_path=None
        c = conn.execute(
            "INSERT INTO clips (site_id, status, recording_path, sound_id) "
            "VALUES (?, 'pending', ?, NULL)",
            (site_id, str(tmp_path / "3.webm")),
        ).lastrowid
        conn.commit()

    finals_dir = tmp_path / "final"
    seen_args: list[tuple] = []

    def fake_compose_clip(recording_path, output_path, sound_path=None, duration_sec=30.0):
        seen_args.append((recording_path, output_path, sound_path, duration_sec))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"fake mp4")

    monkeypatch.setattr(composer, "compose_clip", fake_compose_clip)

    results = composer.compose_pending_clips(
        db_path=db_path, finals_dir=finals_dir, limit=5,
        # NOTE: sound_path intentionally omitted → function should fall
        # back to per-clip sound_audio_path
    )

    assert len(results) == 3
    by_clip = {r["clip_id"]: args for r, args in zip(results, seen_args)}
    rec_a, out_a, snd_a, _ = by_clip[a]
    rec_b, out_b, snd_b, _ = by_clip[b]
    rec_c, out_c, snd_c, _ = by_clip[c]
    # a gets s1, b gets s2, c gets None
    assert snd_a == Path("/tmp/s1.mp3")
    assert snd_b == Path("/tmp/s2.mp3")
    assert snd_c is None
    # Recording paths still correct
    assert str(rec_a) == str(tmp_path / "1.webm")
    assert str(rec_b) == str(tmp_path / "2.webm")
    assert str(rec_c) == str(tmp_path / "3.webm")


def test_compose_pending_clips_caller_sound_path_overrides_per_clip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When the caller passes `sound_path`, it overrides the per-clip value.

    Preserves the legacy "single sound for the whole batch" behavior.
    """
    import sqlite3
    from stumbleupon.db import init_db
    from stumbleupon import composer

    db_path = tmp_path / "stumbleupon.db"
    init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        site_id = conn.execute("INSERT INTO sites (url) VALUES ('https://x.com')").lastrowid
        s1 = conn.execute(
            "INSERT INTO sounds (tiktok_sound_id, audio_path) VALUES ('s1', '/tmp/s1.mp3')"
        ).lastrowid
        a = conn.execute(
            "INSERT INTO clips (site_id, status, recording_path, sound_id) "
            "VALUES (?, 'pending', ?, ?)",
            (site_id, str(tmp_path / "1.webm"), s1),
        ).lastrowid
        conn.commit()

    finals_dir = tmp_path / "final"
    seen_args: list[tuple] = []

    def fake_compose_clip(recording_path, output_path, sound_path=None, duration_sec=30.0):
        seen_args.append((recording_path, output_path, sound_path, duration_sec))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"fake mp4")

    monkeypatch.setattr(composer, "compose_clip", fake_compose_clip)

    override_sound = Path("/tmp/override.mp3")
    composer.compose_pending_clips(
        db_path=db_path, finals_dir=finals_dir, limit=5,
        sound_path=override_sound,
    )

    assert len(seen_args) == 1
    _rec, _out, snd, _dur = seen_args[0]
    assert snd == override_sound
