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
