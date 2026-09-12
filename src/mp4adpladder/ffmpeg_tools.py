"""Locate and validate bundled ffmpeg/ffprobe executables."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class FFmpegError(RuntimeError):
    pass


@dataclass(frozen=True)
class FFmpegTools:
    ffmpeg: Path
    ffprobe: Path
    ffmpeg_version: str
    ffprobe_version: str

    @property
    def directory(self) -> Path:
        return self.ffmpeg.parent


def _run_version(exe: Path) -> str:
    try:
        proc = subprocess.run(
            [str(exe), "-version"],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=CREATE_NO_WINDOW,
        )
    except OSError as exc:
        raise FFmpegError(f"Could not run {exe.name}: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise FFmpegError(f"{exe.name} -version timed out") from exc
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip().splitlines()
        detail = err[0] if err else f"exit {proc.returncode}"
        raise FFmpegError(f"{exe.name} -version failed: {detail}")
    first = (proc.stdout or proc.stderr or "").splitlines()
    return first[0].strip() if first else exe.name


def resolve_tools(ffmpeg_dir: str | Path) -> FFmpegTools:
    folder = Path(ffmpeg_dir)
    if not folder.is_dir():
        raise FFmpegError(f"FFmpeg folder does not exist: {folder}")
    ffmpeg = folder / "ffmpeg.exe"
    ffprobe = folder / "ffprobe.exe"
    missing: list[str] = []
    if not ffmpeg.is_file():
        missing.append(str(ffmpeg))
    if not ffprobe.is_file():
        missing.append(str(ffprobe))
    if missing:
        raise FFmpegError("Missing executable(s):\n" + "\n".join(missing))
    return FFmpegTools(
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
        ffmpeg_version=_run_version(ffmpeg),
        ffprobe_version=_run_version(ffprobe),
    )
