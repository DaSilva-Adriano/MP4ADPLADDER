"""Persist settings in %APPDATA%\\MP4ADPLADDER\\config.json."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mp4adpladder.ladder import FPS_CHOICES, RungState, default_rungs, rungs_from_config

APP_NAME = "MP4ADPLADDER"
DEFAULT_FFMPEG_DIR = r"C:\VSR\ffmpeg-9.0.1-full_build\bin"
CLIP_MODES = ("middle", "start", "end")
VIDEO_EXTENSIONS = (".mkv", ".mp4", ".mov", ".m4v", ".webm", ".avi", ".mxf", ".hevc", ".y4m")


def appdata_dir() -> Path:
    base = os.environ.get("APPDATA")
    if base:
        path = Path(base) / APP_NAME
    else:
        path = Path.home() / "AppData" / "Roaming" / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    return appdata_dir() / "config.json"


def default_fps_targets() -> dict[str, bool]:
    return {str(fps): fps == 24 for fps in FPS_CHOICES}


@dataclass
class AppConfig:
    ffmpeg_dir: str = DEFAULT_FFMPEG_DIR
    rungs: list[RungState] = field(default_factory=default_rungs)
    fps_targets: dict[str, bool] = field(default_factory=default_fps_targets)
    clip_duration_s: float = 10.0
    clip_mode: str = "middle"
    copy_audio: bool = False
    overwrite: bool = False
    allow_upscale: bool = False
    recursive_folder: bool = False
    output_dir: str = ""

    def enabled_fps(self) -> list[int]:
        out: list[int] = []
        for fps in FPS_CHOICES:
            if self.fps_targets.get(str(fps), fps == 24):
                out.append(fps)
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "ffmpeg_dir": self.ffmpeg_dir,
            "rungs": [r.to_dict() for r in self.rungs],
            "fps_targets": {str(k): bool(v) for k, v in self.fps_targets.items()},
            "clip_duration_s": float(self.clip_duration_s),
            "clip_mode": self.clip_mode,
            "copy_audio": bool(self.copy_audio),
            "overwrite": bool(self.overwrite),
            "allow_upscale": bool(self.allow_upscale),
            "recursive_folder": bool(self.recursive_folder),
            "output_dir": self.output_dir,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AppConfig:
        fps_raw = data.get("fps_targets", {})
        fps_targets = default_fps_targets()
        if isinstance(fps_raw, dict):
            for fps in FPS_CHOICES:
                key = str(fps)
                if key in fps_raw:
                    fps_targets[key] = bool(fps_raw[key])
        mode = str(data.get("clip_mode", "middle")).lower()
        if mode not in CLIP_MODES:
            mode = "middle"
        try:
            duration = float(data.get("clip_duration_s", 10.0))
        except (TypeError, ValueError):
            duration = 10.0
        if duration <= 0:
            duration = 10.0
        return cls(
            ffmpeg_dir=str(data.get("ffmpeg_dir", DEFAULT_FFMPEG_DIR) or DEFAULT_FFMPEG_DIR),
            rungs=rungs_from_config(data.get("rungs")),
            fps_targets=fps_targets,
            clip_duration_s=duration,
            clip_mode=mode,
            copy_audio=bool(data.get("copy_audio", False)),
            overwrite=bool(data.get("overwrite", False)),
            allow_upscale=bool(data.get("allow_upscale", False)),
            recursive_folder=bool(data.get("recursive_folder", False)),
            output_dir=str(data.get("output_dir", "") or ""),
        )


def load_config() -> AppConfig:
    path = config_path()
    if not path.is_file():
        cfg = AppConfig()
        save_config(cfg)
        return cfg
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return AppConfig()
        return AppConfig.from_dict(data)
    except (OSError, json.JSONDecodeError):
        return AppConfig()


def save_config(cfg: AppConfig) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg.to_dict(), indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
