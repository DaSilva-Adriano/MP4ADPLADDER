"""ffprobe JSON: duration, width, height, avg_frame_rate, codec."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from mp4adpladder.ffmpeg_tools import CREATE_NO_WINDOW
from mp4adpladder.timefmt import parse_frame_rate

AAC_CODECS = frozenset({"aac", "mp4a"})


class ProbeError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProbeInfo:
    path: Path
    duration_s: float
    width: int
    height: int
    fps: float
    avg_frame_rate: str
    video_codec: str
    audio_codec: str | None
    has_video: bool
    has_audio: bool

    @property
    def audio_is_aac(self) -> bool:
        if not self.audio_codec:
            return False
        name = self.audio_codec.lower()
        return name in AAC_CODECS or name.startswith("aac") or name.startswith("mp4a")

    @property
    def resolution_label(self) -> str:
        if self.width <= 0 or self.height <= 0:
            return "—"
        return f"{self.width}×{self.height}"


def probe_file(ffprobe: Path, source: Path, timeout: float = 60.0) -> ProbeInfo:
    cmd = [
        str(ffprobe),
        "-v",
        "error",
        "-show_format",
        "-show_streams",
        "-of",
        "json",
        str(source),
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
    except OSError as exc:
        raise ProbeError(f"ffprobe failed to start: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ProbeError(f"ffprobe timed out on {source.name}") from exc
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
        raise ProbeError(f"ffprobe error ({source.name}): {err}")
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ProbeError(f"ffprobe returned invalid JSON for {source.name}") from exc

    streams = data.get("streams") or []
    fmt = data.get("format") or {}
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    if video is None:
        raise ProbeError(f"Rejected audio-only (no video stream): {source.name}")

    duration = _duration(fmt, video, audio)
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    rate_text = str(video.get("avg_frame_rate") or video.get("r_frame_rate") or "")
    fps = parse_frame_rate(rate_text)
    vcodec = str(video.get("codec_name") or video.get("codec_tag_string") or "unknown")
    acodec = str(audio.get("codec_name") or "") if audio else None

    return ProbeInfo(
        path=Path(source),
        duration_s=duration,
        width=width,
        height=height,
        fps=fps,
        avg_frame_rate=rate_text,
        video_codec=vcodec,
        audio_codec=acodec or None,
        has_video=True,
        has_audio=audio is not None,
    )


def _duration(fmt: dict, video: dict, audio: dict | None) -> float:
    candidates = [
        fmt.get("duration"),
        video.get("duration"),
        (audio or {}).get("duration"),
    ]
    tags = fmt.get("tags") or {}
    candidates.append(tags.get("DURATION") or tags.get("duration"))
    for raw in candidates:
        if raw is None:
            continue
        try:
            value = float(str(raw).strip())
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 0.0


def collect_video_files(root: Path, recursive: bool, extensions: tuple[str, ...]) -> list[Path]:
    exts = {e.lower() for e in extensions}
    if root.is_file():
        return [root] if root.suffix.lower() in exts else []
    if not root.is_dir():
        return []
    iterator = root.rglob("*") if recursive else root.glob("*")
    files = [p for p in iterator if p.is_file() and p.suffix.lower() in exts]
    files.sort()
    return files
