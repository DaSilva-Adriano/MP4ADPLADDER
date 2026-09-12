"""Worker-thread job queue: files × rungs × fps."""

from __future__ import annotations

import queue
import shutil
import tempfile
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from mp4adpladder.clip import ClipWindow
from mp4adpladder.encode import EncodeJob, EncodeProgress, EncodeResult, ProcHolder, encode_job
from mp4adpladder.ffmpeg_tools import FFmpegTools
from mp4adpladder.ladder import RungState
from mp4adpladder.naming import output_name, sanitize_stem
from mp4adpladder.probe import ProbeInfo
from mp4adpladder.timefmt import fps_is_allowed

UiCallback = Callable[[tuple], None]


@dataclass
class SourceEntry:
    path: Path
    probe: ProbeInfo | None = None
    clip: ClipWindow | None = None
    status: str = "probing"
    error: str = ""


@dataclass
class RunStats:
    total: int = 0
    done: int = 0
    skipped: int = 0
    failed: int = 0
    cancelled: int = 0


class JobController:
    def __init__(self, emit: UiCallback) -> None:
        self._emit = emit
        self._thread: threading.Thread | None = None
        self._jobs: queue.Queue[EncodeJob] = queue.Queue()
        self.cancel = threading.Event()
        self.pause = threading.Event()
        self.holder = ProcHolder()
        self.busy = False
        self.stats = RunStats()

    @property
    def running(self) -> bool:
        t = self._thread
        return t is not None and t.is_alive()

    def start(self, jobs: list[EncodeJob], tools: FFmpegTools) -> None:
        if self.running:
            return
        self.cancel.clear()
        self.pause.clear()
        self.stats = RunStats(total=len(jobs))
        self._jobs = queue.Queue()
        for job in jobs:
            self._jobs.put(job)
        self.busy = True
        self._thread = threading.Thread(
            target=self._worker,
            args=(tools,),
            name="mp4adpladder-worker",
            daemon=True,
        )
        self._thread.start()

    def request_pause(self) -> None:
        self.pause.set()

    def request_resume(self) -> None:
        self.pause.clear()

    def request_cancel(self) -> None:
        self.cancel.set()
        self.pause.clear()
        self.holder.kill()

    def _worker(self, tools: FFmpegTools) -> None:
        try:
            pause_notified = False
            while not self.cancel.is_set():
                if self.pause.is_set():
                    if not pause_notified:
                        self._emit(("paused",))
                        pause_notified = True
                    threading.Event().wait(0.15)
                    continue
                pause_notified = False
                try:
                    job = self._jobs.get_nowait()
                except queue.Empty:
                    break
                self._run_one(tools, job)
            if self.cancel.is_set():
                leftover: list[EncodeJob] = []
                while True:
                    try:
                        leftover.append(self._jobs.get_nowait())
                    except queue.Empty:
                        break
                for job in leftover:
                    self.stats.cancelled += 1
                    self._emit(("job_cancelled", job, "Cancelled before start"))
        finally:
            self.busy = False
            self._emit(("run_finished", self.stats))

    def _run_one(self, tools: FFmpegTools, job: EncodeJob) -> None:
        if self.cancel.is_set():
            self.stats.cancelled += 1
            self._emit(("job_cancelled", job, "Cancelled"))
            return
        self._emit(("job_started", job))
        work = Path(tempfile.mkdtemp(prefix=f"mp4adpladder_{job.job_id}_"))
        try:

            def on_progress(prog: EncodeProgress) -> None:
                self._emit(("job_progress", job, prog))

            result = encode_job(
                tools.ffmpeg,
                job,
                work,
                self.cancel,
                self.holder,
                on_progress,
            )
        except Exception as exc:  # noqa: BLE001 — surface any encode fault to the UI
            result = EncodeResult(ok=False, message=str(exc))
        finally:
            shutil.rmtree(work, ignore_errors=True)

        if result.cancelled:
            self.stats.cancelled += 1
            self._emit(("job_cancelled", job, result.message))
        elif result.ok:
            self.stats.done += 1
            self._emit(("job_done", job, result))
        else:
            self.stats.failed += 1
            self._emit(("job_error", job, result))


def plan_jobs(
    sources: list[SourceEntry],
    rungs: list[RungState],
    fps_targets: list[int],
    output_dir: Path,
    overwrite: bool,
    allow_upscale: bool,
    copy_audio: bool,
    log: Callable[[str], None],
) -> list[EncodeJob]:
    jobs: list[EncodeJob] = []
    enabled_rungs = [r for r in rungs if r.enabled and r.bitrate_k > 0]
    if not enabled_rungs:
        log("No enabled rungs with bitrate > 0.")
        return jobs
    if not fps_targets:
        log("No FPS targets checked.")
        return jobs

    for src in sources:
        info = src.probe
        if info is None:
            if src.status == "probing":
                log(f"Skip {src.path.name}: still probing")
            elif src.error:
                log(f"Skip {src.path.name}: {src.error}")
            else:
                log(f"Skip {src.path.name}: not probed")
            continue
        if not info.has_video:
            log(f"Skip {src.path.name}: audio-only")
            continue
        clip = src.clip
        if clip is None or clip.duration_s <= 0:
            log(f"Skip {src.path.name}: invalid clip window")
            continue
        stem = sanitize_stem(src.path.name)
        audio_mode = "none"
        if copy_audio:
            if not info.has_audio:
                log(f"{src.path.name}: copy audio checked but no audio stream — encoding video only")
            elif info.audio_is_aac:
                audio_mode = "copy"
            else:
                audio_mode = "aac"
                log(
                    f"{src.path.name}: audio codec {info.audio_codec!r} is not AAC — transcoding to AAC 128k"
                )

        for rung in enabled_rungs:
            too_wide = info.width > 0 and info.width < rung.width
            too_tall = info.height > 0 and info.height < rung.height
            if too_wide or too_tall:
                msg = (
                    f"{src.path.name}: source {info.width}x{info.height} < "
                    f"{rung.id} {rung.width}x{rung.height}"
                )
                if not allow_upscale:
                    log(f"Skip {rung.id} for {src.path.name}: {msg} (upscale disabled)")
                    continue
                log(f"Allow upscale: {msg}")

            for fps in fps_targets:
                if not fps_is_allowed(info.fps, fps):
                    log(
                        f"Skip {fps}fps for {src.path.name}: source "
                        f"{info.fps:.3f} fps < {fps} - 0.5 (never up-convert fps)"
                    )
                    continue
                name = output_name(stem, rung.id, fps)
                dest = output_dir / name
                if dest.exists() and not overwrite:
                    log(f"Skip existing {name} (overwrite off)")
                    continue
                jobs.append(
                    EncodeJob(
                        job_id=uuid.uuid4().hex[:12],
                        source=src.path,
                        source_name=src.path.name,
                        rung_id=rung.id,
                        width=rung.width,
                        height=rung.height,
                        bitrate_k=rung.bitrate_k,
                        fps=fps,
                        clip_start=clip.start_s,
                        clip_duration=clip.duration_s,
                        output=dest,
                        audio_mode=audio_mode,
                    )
                )
    return jobs
