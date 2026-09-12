"""Output filename sanitization: {stem}_adp-{rung}-{fps}fps.mp4"""

from __future__ import annotations

from pathlib import Path

_INVALID = '<>:"/\\|?*'


def sanitize_stem(name: str) -> str:
    base = str(name).replace("\\", "/").rsplit("/", 1)[-1]
    stem = base.rsplit(".", 1)[0] if "." in base and not base.startswith(".") else base
    cleaned = stem
    for ch in _INVALID:
        cleaned = cleaned.replace(ch, "_")
    cleaned = "".join(c if c.isprintable() else "_" for c in cleaned)
    cleaned = cleaned.strip(" .")
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned or "source"


def output_name(stem: str, rung_id: str, fps: int) -> str:
    return f"{sanitize_stem(stem)}_adp-{rung_id}-{int(fps)}fps.mp4"


def default_output_dir(first_source: Path) -> Path:
    return first_source.parent / "output"
