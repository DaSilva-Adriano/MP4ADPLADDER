"""Timecode and frame-rate helpers."""

from __future__ import annotations

from fractions import Fraction


def parse_frame_rate(value: str | None) -> float:
    if not value:
        return 0.0
    text = str(value).strip()
    if not text or text in {"0/0", "N/A", "nan"}:
        return 0.0
    try:
        if "/" in text:
            num, den = text.split("/", 1)
            frac = Fraction(int(num), int(den))
            return float(frac) if frac.denominator != 0 else 0.0
        return float(text)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def format_fps(fps: float) -> str:
    if fps <= 0:
        return "—"
    if abs(fps - round(fps)) < 0.001:
        return str(int(round(fps)))
    return f"{fps:.3f}".rstrip("0").rstrip(".")


def format_hms(seconds: float) -> str:
    if seconds < 0 or seconds != seconds:  # NaN
        seconds = 0.0
    total_ms = int(round(seconds * 1000.0))
    if total_ms < 0:
        total_ms = 0
    ms = total_ms % 1000
    total_s = total_ms // 1000
    s = total_s % 60
    total_m = total_s // 60
    m = total_m % 60
    h = total_m // 60
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"
    return f"{m:02d}:{s:02d}.{ms:03d}"


def fps_is_allowed(source_fps: float, target_fps: int) -> bool:
    """Never up-convert. 23.976 may target 24 because 23.976 >= 24 - 0.5."""
    if source_fps <= 0:
        return False
    return source_fps >= (target_fps - 0.5)
