"""Composer: take a recording (webm) and optionally a sound (mp3), run ffmpeg
to produce the final 1080x1920 H.264/AAC mp4.

The pure-logic parts (ffmpeg command construction, output path resolution)
are unit-tested. The subprocess call is exercised with mocked
subprocess.run; ffmpeg itself is not invoked in tests.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


# Default ffmpeg invocation parameters
_DEFAULT_DURATION_SEC = 30.0
_VIDEO_CODEC = "libx264"
_AUDIO_CODEC = "aac"
_OUTPUT_WIDTH = 1080
_OUTPUT_HEIGHT = 1920
_SOUND_VOLUME = 0.7

# Letterboxing filter: scale to fit within 1080x1920, pad to fill the rest with black
_SCALE_FILTER = (
    f"scale={_OUTPUT_WIDTH}:{_OUTPUT_HEIGHT}:force_original_aspect_ratio=decrease,"
    f"pad={_OUTPUT_WIDTH}:{_OUTPUT_HEIGHT}:(ow-iw)/2:(oh-ih)/2:black"
)


def build_ffmpeg_command(
    recording_path: Path,
    output_path: Path,
    sound_path: Path | None = None,
    duration_sec: float = _DEFAULT_DURATION_SEC,
) -> list[str]:
    """Build the ffmpeg command list. Pure function.

    With sound: two inputs (recording + sound), mix in the sound at 70% volume.
    Without sound: just the video, audio dropped with -an.
    Output is H.264 (libx264) video, AAC audio (when sound present), 1080x1920.
    """
    cmd: list[str] = [
        "ffmpeg", "-y",
        "-i", str(recording_path),
    ]

    if sound_path is not None:
        cmd.extend(["-i", str(sound_path)])
        cmd.extend([
            "-t", str(duration_sec),
            "-vf", _SCALE_FILTER,
            "-c:v", _VIDEO_CODEC,
            "-c:a", _AUDIO_CODEC,
            "-map", "0:v:0",  # video from recording
            "-map", "1:a:0",  # audio from sound
            "-af", f"volume={_SOUND_VOLUME}",
            "-shortest",
        ])
    else:
        # No sound: video only, audio disabled
        cmd.extend([
            "-t", str(duration_sec),
            "-vf", _SCALE_FILTER,
            "-c:v", _VIDEO_CODEC,
            "-an",  # disable audio
        ])

    cmd.append(str(output_path))
    return cmd


def resolve_output_path(finals_dir: Path, clip_id: int) -> Path:
    """Return the path where the composer's mp4 for `clip_id` should be written."""
    return Path(finals_dir) / f"{clip_id}.mp4"


def compose_clip(
    recording_path: Path,
    output_path: Path,
    sound_path: Path | None = None,
    duration_sec: float = _DEFAULT_DURATION_SEC,
) -> None:
    """Compose a final mp4 from a recording and (optionally) a sound.

    Calls ffmpeg via subprocess. The output directory is created if it
    doesn't exist. Raises subprocess.CalledProcessError on ffmpeg failure.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = build_ffmpeg_command(recording_path, output_path, sound_path, duration_sec)
    subprocess.run(cmd, check=True, capture_output=True)
