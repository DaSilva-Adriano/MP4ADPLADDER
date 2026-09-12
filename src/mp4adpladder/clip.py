"""Clip-window calculation: middle / start / end of the source."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ClipWindow:
    start_s: float
    duration_s: float

    @property
    def end_s(self) -> float:
        return self.start_s + self.duration_s

    def label(self) -> str:
        from mp4adpladder.timefmt import format_hms

        return f"{format_hms(self.start_s)} → {format_hms(self.end_s)}"


def compute_clip(source_duration_s: float, window_s: float, mode: str) -> ClipWindow:
    """Return the in/out window for a source.

    If the source is shorter than T (or duration is unknown/zero), use the full source.
    Middle: start = max(0, duration/2 - T/2).
    Start: -ss 0 -t T.
    End: start = max(0, source_dur - T).
    """
    src = float(source_duration_s) if source_duration_s and source_duration_s > 0 else 0.0
    want = float(window_s) if window_s and window_s > 0 else 0.0
    if src <= 0:
        # Unknown length: still request T from 0; ffmpeg -t will stop at EOF.
        return ClipWindow(0.0, want if want > 0 else 0.0)
    if want <= 0 or src <= want:
        return ClipWindow(0.0, src)

    key = (mode or "middle").lower()
    if key == "start":
        start = 0.0
    elif key == "end":
        start = max(0.0, src - want)
    else:
        start = max(0.0, src / 2.0 - want / 2.0)

    if start + want > src:
        start = max(0.0, src - want)
    duration = min(want, src - start)
    return ClipWindow(start, duration)


def seek_args(start_s: float, duration_s: float, preroll_s: float = 10.0) -> tuple[list[str], list[str]]:
    """Fast input -ss plus accurate post-input -ss.

    Input-side -ss jumps near a keyframe. A second -ss after -i then decodes
    to the exact in-point (covers GOP drift well beyond 0.25 s when preroll
    is at least one GOP). -t is applied on the output side.
    """
    post: list[str] = []
    pre: list[str] = []
    start = max(0.0, float(start_s))
    duration = max(0.0, float(duration_s))
    preroll = max(0.0, float(preroll_s))

    if start <= 0.001:
        if duration > 0:
            post.extend(["-t", f"{duration:.3f}"])
        return pre, post

    pre_ss = max(0.0, start - preroll)
    accurate = start - pre_ss
    if pre_ss > 0.001:
        pre.extend(["-ss", f"{pre_ss:.3f}"])
    if accurate > 0.001:
        post.extend(["-ss", f"{accurate:.3f}"])
    if duration > 0:
        post.extend(["-t", f"{duration:.3f}"])
    return pre, post
