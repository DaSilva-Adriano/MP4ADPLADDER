"""libx265 encode: 2-pass ABR or single-pass CRF, source → one ladder rung."""

from __future__ import annotations

import queue
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from mp4adpladder.clip import seek_args
from mp4adpladder.ffmpeg_tools import CREATE_NO_WINDOW
from mp4adpladder.ladder import even_dim

NUL = "NUL"


@dataclass
class EncodeJob:
    job_id: str
    source: Path
    source_name: str
    rung_id: str
    width: int
    height: int
    bitrate_k: int
    fps: int
    clip_start: float
    clip_duration: float
    output: Path
    audio_mode: str  # "none" | "copy" | "aac"
    mode: str = "abr"  # "abr" | "crf" | "lossless"
    crf: float = 18.0
    apply_clip: bool = True
    pass_index: int = 1

    @property
    def pass_total(self) -> int:
        return 1 if self.mode in {"crf", "lossless"} else 2


@dataclass
class EncodeProgress:
    pass_index: int
    pass_total: int = 2
    frame: int | None = None
    fps: str = ""
    speed: str = ""
    time_s: float | None = None
    last_line: str = ""
    fraction: float = 0.0  # 0..1 within this pass


@dataclass
class EncodeResult:
    ok: bool
    skipped: bool = False
    cancelled: bool = False
    message: str = ""
    returncode: int = 0


class ProcHolder:
    def __init__(self) -> None:
        self.proc: subprocess.Popen[str] | None = None
        self.lock = threading.Lock()

    def set(self, proc: subprocess.Popen[str] | None) -> None:
        with self.lock:
            self.proc = proc

    def kill(self) -> None:
        with self.lock:
            proc = self.proc
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.terminate()
        except OSError:
            return
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except OSError:
                pass


def maxrate_k(bitrate_k: int) -> int:
    return max(1, (int(bitrate_k) * 5) // 4)  # 1.25x


def bufsize_k(bitrate_k: int) -> int:
    return max(2, int(bitrate_k) * 2)


def _filtergraph(width: int, height: int, fps: int) -> str:
    w = even_dim(max(2, width))
    h = even_dim(max(2, height))
    return f"scale={w}:{h}:flags=lanczos,fps={int(fps)}"


def _lossless_filtergraph(width: int, height: int, fps: int) -> str:
    w = even_dim(max(2, width))
    h = even_dim(max(2, height))
    return f"zscale={w}:{h}:filter=lanczos,fps={int(fps)}"


def _x265_params(pass_index: int, stats_name: str) -> str:
    return f"pass={pass_index}:stats={stats_name}"


def _fmt_crf(crf: float) -> str:
    if abs(crf - round(crf)) < 1e-6:
        return str(int(round(crf)))
    return f"{crf:.2f}".rstrip("0").rstrip(".")


def _audio_args(job: EncodeJob) -> list[str]:
    if job.audio_mode == "copy":
        return ["-map", "0:a:0?", "-c:a", "copy"]
    if job.audio_mode == "aac":
        return ["-map", "0:a:0?", "-c:a", "aac", "-b:a", "128k"]
    return ["-an"]


def build_ffmpeg_cmd(
    ffmpeg: Path,
    job: EncodeJob,
    pass_index: int,
    stats_name: str,
    output_target: str,
) -> list[str]:
    if job.apply_clip:
        pre, post = seek_args(job.clip_start, job.clip_duration)
    else:
        pre, post = [], []
    cmd: list[str] = [
        str(ffmpeg),
        "-hide_banner",
        "-y",
        "-nostdin",
        "-progress",
        "pipe:1",
        "-nostats",
    ]
    cmd.extend(pre)
    cmd.extend(["-i", str(job.source)])
    cmd.extend(post)
    if job.mode == "lossless":
        cmd.extend(
            [
                "-map",
                "0:v:0",
                "-vf",
                _lossless_filtergraph(job.width, job.height, job.fps),
                "-c:v",
                "libx265",
                "-x265-params",
                "lossless=1",
                "-tag:v",
                "hvc1",
            ]
        )
        cmd.extend(_audio_args(job))
        cmd.extend(["-movflags", "+faststart", output_target])
        return cmd

    vf = _filtergraph(job.width, job.height, job.fps)
    cmd.extend(
        [
            "-map",
            "0:v:0",
            "-vf",
            vf,
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx265",
            "-preset",
            "medium",
            "-profile:v",
            "main",
            "-tag:v",
            "hvc1",
        ]
    )
    if job.mode == "crf":
        cmd.extend(["-crf", _fmt_crf(job.crf)])
        cmd.extend(_audio_args(job))
        cmd.extend(["-movflags", "+faststart", output_target])
        return cmd

    b = int(job.bitrate_k)
    cmd.extend(
        [
            "-b:v",
            f"{b}k",
            "-maxrate",
            f"{maxrate_k(b)}k",
            "-bufsize",
            f"{bufsize_k(b)}k",
            "-x265-params",
            _x265_params(pass_index, stats_name),
        ]
    )
    if pass_index == 1:
        cmd.extend(["-an", "-f", "null", NUL])
    else:
        cmd.extend(_audio_args(job))
        cmd.extend(["-movflags", "+faststart", output_target])
    return cmd


def _parse_progress_kv(
    buf: dict[str, str], pass_index: int, duration_s: float, pass_total: int = 2
) -> EncodeProgress:
    frame = None
    if buf.get("frame", "").isdigit():
        frame = int(buf["frame"])
    fps = buf.get("fps", "") or ""
    speed = buf.get("speed", "") or ""
    time_s = None
    if buf.get("out_time_ms"):
        try:
            time_s = int(buf["out_time_ms"]) / 1000.0
        except ValueError:
            time_s = None
    elif buf.get("out_time_us"):
        try:
            time_s = int(buf["out_time_us"]) / 1_000_000.0
        except ValueError:
            time_s = None
    elif buf.get("out_time"):
        time_s = _hms_to_seconds(buf["out_time"])
    fraction = 0.0
    if time_s is not None and duration_s > 0:
        fraction = max(0.0, min(1.0, time_s / duration_s))
    parts = [f"pass {pass_index}/{pass_total}"]
    if fps:
        parts.append(f"fps={fps}")
    if speed:
        parts.append(f"speed={speed}")
    if frame is not None:
        parts.append(f"frame={frame}")
    return EncodeProgress(
        pass_index=pass_index,
        pass_total=pass_total,
        frame=frame,
        fps=fps,
        speed=speed,
        time_s=time_s,
        last_line="  ".join(parts),
        fraction=fraction,
    )


def _hms_to_seconds(text: str) -> float | None:
    try:
        h, m, s = text.split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)
    except (ValueError, TypeError):
        return None


def _reader(pipe, out_q: queue.Queue, split_cr: bool) -> None:
    try:
        if not split_cr:
            for line in iter(pipe.readline, ""):
                out_q.put(line.rstrip("\r\n"))
            return
        buf = ""
        while True:
            chunk = pipe.read(256)
            if not chunk:
                break
            buf += chunk
            while True:
                idx_n = buf.find("\n")
                idx_r = buf.find("\r")
                candidates = [i for i in (idx_n, idx_r) if i >= 0]
                if not candidates:
                    break
                i = min(candidates)
                line, buf = buf[:i], buf[i + 1 :]
                if line:
                    out_q.put(line)
        if buf.strip():
            out_q.put(buf)
    except OSError:
        pass
    finally:
        out_q.put(None)


def _run_one_pass(
    cmd: list[str],
    cwd: Path,
    pass_index: int,
    duration_s: float,
    cancel: threading.Event,
    holder: ProcHolder,
    on_progress,
    pass_total: int = 2,
) -> EncodeResult:
    startupinfo = None
    if hasattr(subprocess, "STARTUPINFO"):
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            startupinfo=startupinfo,
            creationflags=CREATE_NO_WINDOW,
        )
    except OSError as exc:
        return EncodeResult(ok=False, message=f"ffmpeg failed to start: {exc}")

    holder.set(proc)
    stdout_q: queue.Queue[str | None] = queue.Queue()
    stderr_q: queue.Queue[str | None] = queue.Queue()
    threading.Thread(target=_reader, args=(proc.stdout, stdout_q, False), daemon=True).start()
    threading.Thread(target=_reader, args=(proc.stderr, stderr_q, True), daemon=True).start()

    kv: dict[str, str] = {}
    stdout_alive = True
    stderr_alive = True
    err_tail: list[str] = []

    try:
        while stdout_alive or stderr_alive or proc.poll() is None:
            if cancel.is_set():
                holder.kill()
                return EncodeResult(ok=False, cancelled=True, message="Cancelled")
            drained = False
            try:
                while True:
                    item = stdout_q.get_nowait()
                    drained = True
                    if item is None:
                        stdout_alive = False
                        break
                    if "=" in item:
                        key, _, val = item.partition("=")
                        kv[key.strip()] = val.strip()
                        if key.strip() == "progress":
                            on_progress(_parse_progress_kv(kv, pass_index, duration_s, pass_total))
            except queue.Empty:
                pass
            try:
                while True:
                    item = stderr_q.get_nowait()
                    drained = True
                    if item is None:
                        stderr_alive = False
                        break
                    text = item.strip()
                    if text:
                        err_tail.append(text)
                        if len(err_tail) > 40:
                            err_tail.pop(0)
            except queue.Empty:
                pass
            if not drained:
                try:
                    proc.wait(timeout=0.12)
                except subprocess.TimeoutExpired:
                    pass
    finally:
        holder.set(None)

    code = proc.returncode if proc.returncode is not None else -1
    if cancel.is_set():
        return EncodeResult(ok=False, cancelled=True, message="Cancelled", returncode=code)
    if code != 0:
        tail = "\n".join(err_tail[-8:]) or f"ffmpeg exit {code}"
        return EncodeResult(ok=False, message=tail, returncode=code)
    return EncodeResult(ok=True, returncode=0)


def encode_job(
    ffmpeg: Path,
    job: EncodeJob,
    work_dir: Path,
    cancel: threading.Event,
    holder: ProcHolder,
    on_progress,
) -> EncodeResult:
    """ABR: 2-pass. CRF / lossless: single pass to file."""
    work_dir.mkdir(parents=True, exist_ok=True)
    job.output.parent.mkdir(parents=True, exist_ok=True)
    stats_name = "x265-2pass.log"
    out_path = str(job.output)

    if cancel.is_set():
        return EncodeResult(ok=False, cancelled=True, message="Cancelled")

    if job.mode in {"crf", "lossless"}:
        cmd = build_ffmpeg_cmd(ffmpeg, job, 1, stats_name, out_path)
        result = _run_one_pass(
            cmd, work_dir, 1, job.clip_duration, cancel, holder, on_progress, pass_total=1
        )
        if not result.ok:
            _remove_if_exists(job.output)
            return result
        if not job.output.is_file():
            kind = "Lossless" if job.mode == "lossless" else "CRF"
            return EncodeResult(ok=False, message=f"{kind} encode finished but output file is missing")
        return result

    cmd1 = build_ffmpeg_cmd(ffmpeg, job, 1, stats_name, NUL)
    r1 = _run_one_pass(cmd1, work_dir, 1, job.clip_duration, cancel, holder, on_progress, pass_total=2)
    if not r1.ok:
        _remove_if_exists(job.output)
        return r1

    if cancel.is_set():
        _remove_if_exists(job.output)
        return EncodeResult(ok=False, cancelled=True, message="Cancelled")

    cmd2 = build_ffmpeg_cmd(ffmpeg, job, 2, stats_name, out_path)
    r2 = _run_one_pass(cmd2, work_dir, 2, job.clip_duration, cancel, holder, on_progress, pass_total=2)
    if not r2.ok:
        _remove_if_exists(job.output)
        return r2
    if not job.output.is_file():
        return EncodeResult(ok=False, message="Pass 2 finished but output file is missing")
    return r2


def _remove_if_exists(path: Path) -> None:
    try:
        if path.is_file():
            path.unlink()
    except OSError:
        pass
