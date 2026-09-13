"""Output filenames: {stem}_{adp|crf|lossless}-{rung}-{fps}fps.mp4 or {stem}_crf-{value}.mp4."""

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


def format_crf_tag(crf: float) -> str:
    if abs(crf - round(crf)) < 1e-6:
        return str(int(round(crf)))
    return f"{crf:.2f}".rstrip("0").rstrip(".")


def output_name(
    stem: str,
    rung_id: str,
    fps: int,
    crf: float | None = None,
    lossless: bool = False,
    crf_only: bool = False,
) -> str:
    if crf_only:
        return f"{sanitize_stem(stem)}_crf-{format_crf_tag(crf if crf is not None else 18.0)}.mp4"
    if lossless:
        tag = "lossless"
    elif crf is not None:
        tag = "crf"
    else:
        tag = "adp"
    return f"{sanitize_stem(stem)}_{tag}-{rung_id}-{int(fps)}fps.mp4"


def default_output_dir(first_source: Path) -> Path:
    return first_source.parent / "output"
