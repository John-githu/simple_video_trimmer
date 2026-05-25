from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

SUPPORTED_EXTENSIONS = {
    ".mp4",
    ".mkv",
    ".mov",
    ".avi",
    ".wmv",
    ".flv",
    ".m4v",
    ".webm",
}


class TrimError(RuntimeError):
    """Raised when ffmpeg trim execution fails."""


def _decode_process_output(raw: bytes | None) -> str:
    if not raw:
        return ""
    for encoding in ("utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def is_supported_video(filename: str) -> bool:
    return Path(filename).suffix.lower() in SUPPORTED_EXTENSIONS


def parse_time_to_seconds(value: Any) -> float:
    if isinstance(value, (int, float)):
        seconds = float(value)
    elif isinstance(value, str):
        text = value.strip().replace(",", ".")
        if not text:
            raise ValueError("Time value cannot be empty.")

        if ":" in text:
            parts = text.split(":")
            if len(parts) > 3:
                raise ValueError(f"Invalid time format: {value}")
            multipliers = [1, 60, 3600]
            seconds = 0.0
            for index, part in enumerate(reversed(parts)):
                if not part:
                    raise ValueError(f"Invalid time format: {value}")
                seconds += float(part) * multipliers[index]
        else:
            seconds = float(text)
    else:
        raise ValueError(f"Unsupported time value type: {type(value)}")

    if seconds < 0:
        raise ValueError("Time value must be >= 0.")
    return seconds


def format_hhmmss(seconds: float) -> str:
    whole = int(seconds)
    fraction = seconds - whole
    hours = whole // 3600
    minutes = (whole % 3600) // 60
    second_value = (whole % 60) + fraction
    return f"{hours:02d}:{minutes:02d}:{second_value:06.3f}"


def resolve_binary(binary_name: str, preferred_path: str | None = None) -> str:
    if preferred_path:
        preferred = Path(preferred_path)
        if preferred.exists():
            return str(preferred)

    found = shutil.which(binary_name)
    if found:
        return found

    project_root = Path(__file__).resolve().parents[1]
    bin_folder = project_root / "bin"
    windows_candidate = bin_folder / f"{binary_name}.exe"
    generic_candidate = bin_folder / binary_name

    if windows_candidate.exists():
        return str(windows_candidate)
    if generic_candidate.exists():
        return str(generic_candidate)

    raise FileNotFoundError(
        f"Unable to locate binary '{binary_name}'. Configure its path explicitly or place it in PATH."
    )


def probe_video(ffprobe_bin: str, source_path: str) -> dict[str, Any]:
    command = [
        ffprobe_bin,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_entries",
        "format=duration,format_name:stream=codec_type,width,height",
        source_path,
    ]

    result = subprocess.run(command, capture_output=True)
    if result.returncode != 0:
        stderr_text = _decode_process_output(result.stderr).strip()
        raise TrimError(stderr_text or "ffprobe failed.")

    stdout_text = _decode_process_output(result.stdout)
    payload = json.loads(stdout_text or "{}")
    duration_text = (payload.get("format") or {}).get("duration", 0)
    try:
        duration = float(duration_text)
    except (TypeError, ValueError):
        duration = 0.0

    streams = payload.get("streams") or []
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), {})

    return {
        "duration": max(duration, 0.0),
        "width": int(video_stream.get("width") or 0),
        "height": int(video_stream.get("height") or 0),
        "format_name": (payload.get("format") or {}).get("format_name") or "unknown",
    }


def validate_trim_window(duration: float, start: float, end: float) -> None:
    if duration <= 0:
        raise ValueError("Video duration must be > 0.")
    if start < 0:
        raise ValueError("Start time must be >= 0.")
    if end <= start:
        raise ValueError("End time must be greater than start time.")
    if end > duration + 0.001:
        raise ValueError("End time cannot exceed video duration.")


def build_trim_command(
    ffmpeg_bin: str,
    source_path: str,
    output_path: str,
    start: float,
    end: float,
) -> list[str]:
    duration = end - start
    return [
        ffmpeg_bin,
        "-y",
        "-ss",
        f"{start:.3f}",
        "-i",
        source_path,
        "-map",
        "0",
        "-c",
        "copy",
        "-t",
        f"{duration:.3f}",
        output_path,
    ]


def trim_video_copy(
    ffmpeg_bin: str,
    source_path: str,
    output_path: str,
    start: float,
    end: float,
) -> list[str]:
    output_parent = Path(output_path).resolve().parent
    output_parent.mkdir(parents=True, exist_ok=True)

    command = build_trim_command(ffmpeg_bin, source_path, output_path, start, end)
    result = subprocess.run(command, capture_output=True)
    if result.returncode != 0:
        stderr_text = _decode_process_output(result.stderr)
        stderr_tail = stderr_text.strip().splitlines()[-20:]
        detail = "\n".join(stderr_tail) if stderr_tail else "ffmpeg failed"
        raise TrimError(detail)
    return command


def make_output_path(
    output_dir: str,
    source_filename: str,
    start: float,
    end: float,
    requested_name: str | None = None,
) -> str:
    base_dir = Path(output_dir)
    base_dir.mkdir(parents=True, exist_ok=True)

    source_path = Path(source_filename)
    ext = source_path.suffix or ".mp4"

    if requested_name:
        candidate_name = Path(requested_name).name
        if Path(candidate_name).suffix == "":
            candidate_name = f"{candidate_name}{ext}"
    else:
        token = f"{start:.2f}-{end:.2f}".replace(".", "_")
        candidate_name = f"{source_path.stem}_trim_{token}{ext}"

    candidate = base_dir / candidate_name
    counter = 1
    while candidate.exists():
        candidate = base_dir / f"{Path(candidate_name).stem}_{counter}{Path(candidate_name).suffix}"
        counter += 1

    return str(candidate)
