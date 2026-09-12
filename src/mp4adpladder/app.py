"""MP4ADPLADDER desktop UI (CustomTkinter). Jobs run on a worker thread."""

from __future__ import annotations

import ctypes
import os
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

from mp4adpladder import __version__
from mp4adpladder.clip import compute_clip
from mp4adpladder.config import (
    APP_NAME,
    VIDEO_EXTENSIONS,
    AppConfig,
    load_config,
    save_config,
)
from mp4adpladder.encode import EncodeJob, EncodeProgress, EncodeResult
from mp4adpladder.ffmpeg_tools import FFmpegError, FFmpegTools, resolve_tools
from mp4adpladder.jobs import JobController, SourceEntry, plan_jobs
from mp4adpladder.ladder import FPS_CHOICES, default_rungs, format_gb_h
from mp4adpladder.naming import default_output_dir
from mp4adpladder.probe import ProbeError, ProbeInfo, collect_video_files, probe_file
from mp4adpladder.timefmt import format_fps, format_hms

HELP_TEXT = """Bitrates = Tableau 1 midpoints (streaming GB/h), not CRF.
2-pass ABR ≈ constant average rate.
Later VSR reference = the adp-4k file from the SAME source and clip window.

Each rung encodes source → that resolution (no intermediate 4K file).
libx265 Main, yuv420p, -tag:v hvc1, +faststart, 2-pass, -preset medium.
-maxrate 1.25×b:v, -bufsize 2×b:v. Video-only unless Copy audio is checked.

FPS is never up-converted. A target is used only if source_fps ≥ target − 0.5
(so 23.976 may output 24). Checking 60 on a 24 fps source skips 60.

Clip default is 10 s, middle of the file. Seek uses fast input -ss with a 10 s
preroll plus an accurate post-input -ss (covers keyframe drift > 0.25 s).

Outputs: {stem}_adp-{rung}-{fps}fps.mp4  e.g. film_adp-720p-24fps.mp4
"""

VIDEO_FILETYPES = (
    ("Video files", "*.mkv;*.mp4;*.mov;*.m4v;*.webm;*.avi;*.mxf;*.hevc;*.y4m"),
    ("All files", "*.*"),
)

ACCENT = "#C45C26"
BG = "#1A1A1A"
PANEL = "#242424"
ROW = "#2C2C2C"


def _set_windows_app_id() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_NAME)
    except (AttributeError, OSError):
        pass


class MP4ADPLadderApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1280x900")
        self.minsize(1080, 760)
        self.configure(fg_color=BG)

        self.cfg = load_config()
        self.tools: FFmpegTools | None = None
        self.sources: dict[str, SourceEntry] = {}
        self.ui_queue: queue.Queue[tuple] = queue.Queue()
        self.controller = JobController(self.ui_queue.put)
        self._probe_thread: threading.Thread | None = None
        self._current_job: EncodeJob | None = None
        self._file_totals: dict[str, int] = {}
        self._file_finished: dict[str, int] = {}
        self._run_total = 0
        self._run_finished = 0
        self._closing = False

        self.output_var = ctk.StringVar(value=self.cfg.output_dir)
        self.recursive_var = ctk.BooleanVar(value=self.cfg.recursive_folder)
        self.overwrite_var = ctk.BooleanVar(value=self.cfg.overwrite)
        self.copy_audio_var = ctk.BooleanVar(value=self.cfg.copy_audio)
        self.allow_upscale_var = ctk.BooleanVar(value=self.cfg.allow_upscale)
        self.clip_mode_var = ctk.StringVar(value=self.cfg.clip_mode)
        self.clip_dur_var = ctk.StringVar(value=_fmt_num(self.cfg.clip_duration_s))
        self.fps_vars = {
            fps: ctk.BooleanVar(value=self.cfg.fps_targets.get(str(fps), fps == 24))
            for fps in FPS_CHOICES
        }
        self.rung_enable: dict[str, ctk.BooleanVar] = {}
        self.rung_kbps: dict[str, ctk.StringVar] = {}
        self.rung_entries: dict[str, ctk.CTkEntry] = {}
        self.rung_gbh: dict[str, ctk.CTkLabel] = {}

        self._build()
        self._style_tree()
        self._bind_traces()
        self._refresh_tools()
        self.after(80, self._drain_ui)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build(self) -> None:
        pad = {"padx": 12, "pady": (8, 0)}
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=4, minsize=220)
        self.grid_rowconfigure(7, weight=2, minsize=160)

        header = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=8)
        header.grid(row=0, column=0, sticky="ew", **pad)
        header.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            header,
            text=APP_NAME,
            font=ctk.CTkFont(family="Segoe UI", size=20, weight="bold"),
            text_color="#F2EDE6",
        ).grid(row=0, column=0, padx=14, pady=10, sticky="w")
        self.tools_label = ctk.CTkLabel(
            header,
            text="Checking ffmpeg…",
            font=ctk.CTkFont(family="Segoe UI", size=13),
            text_color="#B8B0A8",
            anchor="e",
        )
        self.tools_label.grid(row=0, column=1, padx=14, pady=10, sticky="e")

        tools = ctk.CTkFrame(self, fg_color="transparent")
        tools.grid(row=1, column=0, sticky="ew", **pad)
        for spec in (
            ("Add files", self._add_files),
            ("Add folder", self._add_folder),
        ):
            ctk.CTkButton(tools, text=spec[0], width=110, command=spec[1], fg_color=ACCENT, hover_color="#A34B1E").pack(
                side="left", padx=(0, 8)
            )
        ctk.CTkCheckBox(tools, text="Recursive", variable=self.recursive_var, width=110).pack(side="left", padx=(0, 12))
        ctk.CTkButton(tools, text="Remove", width=90, fg_color="#3A3A3A", hover_color="#4A4A4A", command=self._remove).pack(
            side="left", padx=(0, 8)
        )
        ctk.CTkButton(tools, text="Clear", width=90, fg_color="#3A3A3A", hover_color="#4A4A4A", command=self._clear).pack(
            side="left"
        )

        out = ctk.CTkFrame(self, fg_color="transparent")
        out.grid(row=2, column=0, sticky="ew", **pad)
        out.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(out, text="Output").grid(row=0, column=0, padx=(0, 8))
        ctk.CTkEntry(out, textvariable=self.output_var).grid(row=0, column=1, sticky="ew")
        ctk.CTkButton(out, text="Browse", width=90, fg_color="#3A3A3A", hover_color="#4A4A4A", command=self._browse_output).grid(
            row=0, column=2, padx=8
        )
        ctk.CTkCheckBox(out, text="Overwrite", variable=self.overwrite_var).grid(row=0, column=3)

        files = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=8)
        files.grid(row=3, column=0, sticky="nsew", **pad)
        files.grid_columnconfigure(0, weight=1)
        files.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(files, text="Sources", font=ctk.CTkFont(size=14, weight="bold")).grid(
            row=0, column=0, sticky="w", padx=12, pady=(8, 4)
        )
        tree_host = tk.Frame(files, bg=ROW, highlightthickness=0)
        tree_host.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        tree_host.grid_columnconfigure(0, weight=1)
        tree_host.grid_rowconfigure(0, weight=1)
        cols = ("name", "duration", "fps", "resolution", "clip", "status")
        self.tree = ttk.Treeview(tree_host, columns=cols, show="headings", selectmode="extended", height=10)
        headings = {
            "name": "Name",
            "duration": "Duration",
            "fps": "FPS",
            "resolution": "Resolution",
            "clip": "Clip window",
            "status": "Status",
        }
        widths = {"name": 280, "duration": 100, "fps": 80, "resolution": 110, "clip": 210, "status": 260}
        for key, title in headings.items():
            self.tree.heading(key, text=title)
            self.tree.column(key, width=widths[key], stretch=key in {"name", "status"})
        vsb = ttk.Scrollbar(tree_host, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")

        opts = ctk.CTkFrame(self, fg_color="transparent")
        opts.grid(row=4, column=0, sticky="ew", **pad)
        opts.grid_columnconfigure(0, weight=3)
        opts.grid_columnconfigure(1, weight=2)
        opts.grid_columnconfigure(2, weight=2)
        self._build_ladder(opts)
        self._build_fps_clip(opts)
        self._build_flags(opts)

        prog = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=8)
        prog.grid(row=5, column=0, sticky="ew", **pad)
        prog.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(prog, text="Global").grid(row=0, column=0, padx=12, pady=(10, 2), sticky="w")
        self.global_bar = ctk.CTkProgressBar(prog, height=14, progress_color=ACCENT)
        self.global_bar.grid(row=0, column=1, sticky="ew", padx=(0, 12), pady=(10, 2))
        self.global_bar.set(0)
        self.global_label = ctk.CTkLabel(prog, text="Idle", text_color="#B8B0A8", anchor="w")
        self.global_label.grid(row=1, column=1, sticky="w", padx=(0, 12))
        ctk.CTkLabel(prog, text="File").grid(row=2, column=0, padx=12, pady=(6, 2), sticky="w")
        self.file_bar = ctk.CTkProgressBar(prog, height=14, progress_color="#D89A3A")
        self.file_bar.grid(row=2, column=1, sticky="ew", padx=(0, 12), pady=(6, 2))
        self.file_bar.set(0)
        self.file_label = ctk.CTkLabel(prog, text="", text_color="#B8B0A8", anchor="w")
        self.file_label.grid(row=3, column=1, sticky="w", padx=(0, 12))
        self.ffmpeg_line = ctk.CTkLabel(
            prog,
            text="",
            text_color="#E6D5C3",
            anchor="w",
            font=ctk.CTkFont(family="Consolas", size=12),
        )
        self.ffmpeg_line.grid(row=4, column=0, columnspan=2, sticky="ew", padx=12, pady=(4, 10))

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.grid(row=6, column=0, sticky="ew", **pad)
        self.start_btn = ctk.CTkButton(
            actions, text="Start", width=110, fg_color=ACCENT, hover_color="#A34B1E", command=self._start
        )
        self.start_btn.pack(side="left", padx=(0, 8))
        self.pause_btn = ctk.CTkButton(
            actions,
            text="Pause (finish current)",
            width=190,
            fg_color="#3A3A3A",
            hover_color="#4A4A4A",
            command=self._pause,
            state="disabled",
        )
        self.pause_btn.pack(side="left", padx=(0, 8))
        self.cancel_btn = ctk.CTkButton(
            actions,
            text="Cancel",
            width=110,
            fg_color="#6B2A22",
            hover_color="#8A352B",
            command=self._cancel,
            state="disabled",
        )
        self.cancel_btn.pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            actions, text="Open output folder", width=160, fg_color="#3A3A3A", hover_color="#4A4A4A", command=self._open_output
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            actions, text="Settings", width=100, fg_color="#3A3A3A", hover_color="#4A4A4A", command=self._open_settings
        ).pack(side="right")
        ctk.CTkButton(
            actions, text="Help", width=90, fg_color="#3A3A3A", hover_color="#4A4A4A", command=self._open_help
        ).pack(side="right", padx=(0, 8))

        log_frame = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=8)
        log_frame.grid(row=7, column=0, sticky="nsew", padx=12, pady=(8, 12))
        log_frame.grid_columnconfigure(0, weight=1)
        log_frame.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(log_frame, text="Log", font=ctk.CTkFont(size=14, weight="bold")).grid(
            row=0, column=0, sticky="w", padx=12, pady=(8, 4)
        )
        self.log_box = ctk.CTkTextbox(
            log_frame, font=ctk.CTkFont(family="Consolas", size=12), wrap="word", height=160
        )
        self.log_box.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        self.log_box.configure(state="disabled")

    def _build_ladder(self, parent: ctk.CTkFrame) -> None:
        frame = ctk.CTkFrame(parent, fg_color=PANEL, corner_radius=8)
        frame.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        ctk.CTkLabel(frame, text="Ladder", font=ctk.CTkFont(size=14, weight="bold")).grid(
            row=0, column=0, columnspan=4, sticky="w", padx=12, pady=(8, 2)
        )
        headers = ("Resolution", "Target kbps", "GB/h", "On")
        for i, h in enumerate(headers):
            ctk.CTkLabel(frame, text=h, text_color="#B8B0A8", font=ctk.CTkFont(size=12)).grid(
                row=1, column=i, padx=8, sticky="w"
            )
        for idx, rung in enumerate(self.cfg.rungs):
            row = idx + 2
            enable = ctk.BooleanVar(value=rung.enabled)
            kbps = ctk.StringVar(value=str(rung.bitrate_k))
            self.rung_enable[rung.id] = enable
            self.rung_kbps[rung.id] = kbps
            ctk.CTkLabel(frame, text=f"{rung.id}  {rung.size_label}").grid(row=row, column=0, padx=8, pady=2, sticky="w")
            entry = ctk.CTkEntry(frame, textvariable=kbps, width=80, justify="right")
            entry.grid(row=row, column=1, padx=8, pady=2, sticky="w")
            entry.bind("<FocusOut>", lambda _e, rid=rung.id: self._validate_kbps(rid))
            self.rung_entries[rung.id] = entry
            gbh = ctk.CTkLabel(frame, text=format_gb_h(rung.bitrate_k), text_color="#B8B0A8")
            gbh.grid(row=row, column=2, padx=8, pady=2, sticky="w")
            self.rung_gbh[rung.id] = gbh
            kbps.trace_add("write", lambda *_args, rid=rung.id: self._update_gbh(rid))
            ctk.CTkCheckBox(frame, text="", variable=enable, width=28).grid(row=row, column=3, padx=8, pady=2)
        ctk.CTkButton(
            frame,
            text="Reset to defaults",
            width=150,
            fg_color="#3A3A3A",
            hover_color="#4A4A4A",
            command=self._reset_ladder,
        ).grid(row=8, column=0, columnspan=4, padx=12, pady=(6, 10), sticky="w")

    def _build_fps_clip(self, parent: ctk.CTkFrame) -> None:
        frame = ctk.CTkFrame(parent, fg_color=PANEL, corner_radius=8)
        frame.grid(row=0, column=1, sticky="nsew", padx=(0, 8))
        ctk.CTkLabel(frame, text="FPS", font=ctk.CTkFont(size=14, weight="bold")).pack(
            anchor="w", padx=12, pady=(8, 4)
        )
        fps_row = ctk.CTkFrame(frame, fg_color="transparent")
        fps_row.pack(anchor="w", padx=12)
        for fps in FPS_CHOICES:
            ctk.CTkCheckBox(fps_row, text=str(fps), variable=self.fps_vars[fps], width=70).pack(side="left", padx=(0, 6))
        ctk.CTkLabel(frame, text="Clip window", font=ctk.CTkFont(size=14, weight="bold")).pack(
            anchor="w", padx=12, pady=(10, 4)
        )
        dur_row = ctk.CTkFrame(frame, fg_color="transparent")
        dur_row.pack(anchor="w", padx=12)
        ctk.CTkLabel(dur_row, text="Duration (s)").pack(side="left", padx=(0, 8))
        dur = ctk.CTkEntry(dur_row, textvariable=self.clip_dur_var, width=70)
        dur.pack(side="left")
        dur.bind("<FocusOut>", lambda _e: self._refresh_clips())
        dur.bind("<Return>", lambda _e: self._refresh_clips())
        radios = ctk.CTkFrame(frame, fg_color="transparent")
        radios.pack(anchor="w", padx=12, pady=(8, 12))
        for value, label in (("middle", "Middle"), ("start", "Start"), ("end", "End")):
            ctk.CTkRadioButton(
                radios,
                text=label,
                value=value,
                variable=self.clip_mode_var,
                command=self._refresh_clips,
            ).pack(side="left", padx=(0, 10))

    def _build_flags(self, parent: ctk.CTkFrame) -> None:
        frame = ctk.CTkFrame(parent, fg_color=PANEL, corner_radius=8)
        frame.grid(row=0, column=2, sticky="nsew")
        ctk.CTkLabel(frame, text="Options", font=ctk.CTkFont(size=14, weight="bold")).pack(
            anchor="w", padx=12, pady=(8, 8)
        )
        ctk.CTkCheckBox(frame, text="Copy audio (AAC 128k / copy if AAC)", variable=self.copy_audio_var).pack(
            anchor="w", padx=12, pady=4
        )
        ctk.CTkCheckBox(frame, text="Allow upscale", variable=self.allow_upscale_var).pack(
            anchor="w", padx=12, pady=4
        )

    def _style_tree(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure(
            "Treeview",
            background="#2A2A2A",
            foreground="#F2EDE6",
            fieldbackground="#2A2A2A",
            rowheight=26,
            borderwidth=0,
            font=("Segoe UI", 10),
        )
        style.configure(
            "Treeview.Heading",
            background="#333333",
            foreground="#F2EDE6",
            relief="flat",
            font=("Segoe UI", 10, "bold"),
        )
        style.map("Treeview", background=[("selected", "#8A3E1C")], foreground=[("selected", "#FFFFFF")])
        style.map("Treeview.Heading", background=[("active", "#3F3F3F")])

    def _bind_traces(self) -> None:
        self.clip_mode_var.trace_add("write", lambda *_: self._refresh_clips())
        self.output_var.trace_add("write", lambda *_: self._persist())
        self.recursive_var.trace_add("write", lambda *_: self._persist())
        self.overwrite_var.trace_add("write", lambda *_: self._persist())
        self.copy_audio_var.trace_add("write", lambda *_: self._persist())
        self.allow_upscale_var.trace_add("write", lambda *_: self._persist())
        for var in self.fps_vars.values():
            var.trace_add("write", lambda *_: self._persist())
        for var in self.rung_enable.values():
            var.trace_add("write", lambda *_: self._persist())

    def _log(self, message: str) -> None:
        from datetime import datetime

        line = f"[{datetime.now().strftime('%H:%M:%S')}] {message}\n"
        self.log_box.configure(state="normal")
        self.log_box.insert("end", line)
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _snapshot(self) -> AppConfig:
        rungs = default_rungs()
        by_id = {r.id: r for r in rungs}
        out_rungs = []
        for rung in self.cfg.rungs:
            item = by_id.get(rung.id, rung)
            item.enabled = bool(self.rung_enable[rung.id].get())
            item.bitrate_k = _parse_kbps(self.rung_kbps[rung.id].get(), item.bitrate_k)
            out_rungs.append(item)
        try:
            duration = float(self.clip_dur_var.get().replace(",", "."))
        except ValueError:
            duration = 10.0
        if duration <= 0:
            duration = 10.0
        return AppConfig(
            ffmpeg_dir=self.cfg.ffmpeg_dir,
            rungs=out_rungs,
            fps_targets={str(fps): bool(self.fps_vars[fps].get()) for fps in FPS_CHOICES},
            clip_duration_s=duration,
            clip_mode=self.clip_mode_var.get() or "middle",
            copy_audio=bool(self.copy_audio_var.get()),
            overwrite=bool(self.overwrite_var.get()),
            allow_upscale=bool(self.allow_upscale_var.get()),
            recursive_folder=bool(self.recursive_var.get()),
            output_dir=self.output_var.get().strip(),
        )

    def _persist(self) -> None:
        try:
            self.cfg = self._snapshot()
            save_config(self.cfg)
        except Exception:
            pass

    def _update_gbh(self, rung_id: str) -> None:
        label = self.rung_gbh.get(rung_id)
        if label is None:
            return
        defaults = {r.id: r.bitrate_k for r in default_rungs()}
        value = _parse_kbps(self.rung_kbps[rung_id].get(), defaults.get(rung_id, 1))
        label.configure(text=format_gb_h(value))

    def _validate_kbps(self, rung_id: str) -> None:
        defaults = {r.id: r.bitrate_k for r in default_rungs()}
        raw = self.rung_kbps[rung_id].get()
        value = _parse_kbps(raw, defaults.get(rung_id, 1))
        self.rung_kbps[rung_id].set(str(value))
        self._update_gbh(rung_id)
        self._persist()

    def _reset_ladder(self) -> None:
        for rung in default_rungs():
            self.rung_enable[rung.id].set(True)
            self.rung_kbps[rung.id].set(str(rung.bitrate_k))
        self._persist()
        self._log("Ladder reset to Tableau 1 midpoint bitrates.")

    def _clip_duration(self) -> float:
        try:
            value = float(self.clip_dur_var.get().replace(",", "."))
        except ValueError:
            value = 10.0
        return value if value > 0 else 10.0

    def _refresh_clips(self) -> None:
        mode = self.clip_mode_var.get() or "middle"
        duration = self._clip_duration()
        self.clip_dur_var.set(_fmt_num(duration))
        for key, src in self.sources.items():
            if src.probe is None:
                continue
            src.clip = compute_clip(src.probe.duration_s, duration, mode)
            if self.tree.exists(key):
                self.tree.set(key, "clip", src.clip.label())
        self._persist()

    def _refresh_tools(self) -> None:
        try:
            self.tools = resolve_tools(self.cfg.ffmpeg_dir)
        except FFmpegError as exc:
            self.tools = None
            self.tools_label.configure(text="ffmpeg / ffprobe missing — open Settings", text_color="#E07070")
            self._log(str(exc))
            return
        self.tools_label.configure(
            text=self.tools.ffmpeg_version.replace("Copyright", "—").split("—")[0].strip(),
            text_color="#8FCB7A",
        )
        self._log(f"Using {self.tools.ffmpeg}")
        self._log(f"Using {self.tools.ffprobe}")

    def _add_files(self) -> None:
        paths = filedialog.askopenfilenames(parent=self, title="Add video files", filetypes=VIDEO_FILETYPES)
        if not paths:
            return
        self._ingest([Path(p) for p in paths])

    def _add_folder(self) -> None:
        folder = filedialog.askdirectory(parent=self, title="Add folder")
        if not folder:
            return
        recursive = bool(self.recursive_var.get())
        files = collect_video_files(Path(folder), recursive, VIDEO_EXTENSIONS)
        if not files:
            messagebox.showinfo(APP_NAME, "No matching video files in that folder.", parent=self)
            return
        self._ingest(files)

    def _ingest(self, paths: list[Path]) -> None:
        added: list[Path] = []
        for raw in paths:
            path = raw.resolve()
            if path.suffix.lower() not in VIDEO_EXTENSIONS:
                continue
            key = str(path)
            if key in self.sources:
                continue
            self.sources[key] = SourceEntry(path=path, status="probing")
            self.tree.insert(
                "",
                "end",
                iid=key,
                values=(path.name, "…", "…", "…", "…", "probing"),
            )
            added.append(path)
        if not added:
            return
        if not self.output_var.get().strip():
            self.output_var.set(str(default_output_dir(added[0])))
        self._log(f"Added {len(added)} file(s) — probing…")
        self._start_probe(added)

    def _start_probe(self, paths: list[Path]) -> None:
        if self.tools is None:
            for path in paths:
                key = str(path.resolve())
                self._set_status(key, "error: ffmpeg not configured")
            return

        ffprobe = self.tools.ffprobe

        def work() -> None:
            for path in paths:
                try:
                    info = probe_file(ffprobe, path)
                    self.ui_queue.put(("probed", path, info))
                except ProbeError as exc:
                    self.ui_queue.put(("probe_error", path, str(exc)))
                except Exception as exc:  # noqa: BLE001
                    self.ui_queue.put(("probe_error", path, f"{exc}"))

        threading.Thread(target=work, name="mp4adpladder-probe", daemon=True).start()

    def _remove(self) -> None:
        selected = list(self.tree.selection())
        for key in selected:
            self.tree.delete(key)
            self.sources.pop(key, None)
        if selected:
            self._log(f"Removed {len(selected)} file(s)")

    def _clear(self) -> None:
        for key in list(self.sources):
            if self.tree.exists(key):
                self.tree.delete(key)
        self.sources.clear()
        self._log("File list cleared")

    def _browse_output(self) -> None:
        folder = filedialog.askdirectory(parent=self, title="Output folder")
        if folder:
            self.output_var.set(folder)

    def _open_output(self) -> None:
        raw = self.output_var.get().strip()
        if not raw:
            messagebox.showinfo(APP_NAME, "Set an output folder first.", parent=self)
            return
        path = Path(raw)
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Cannot create output folder:\n{exc}", parent=self)
            return
        os.startfile(path)  # type: ignore[attr-defined]

    def _running_controls(self, running: bool) -> None:
        self.start_btn.configure(state="disabled" if running else "normal")
        self.pause_btn.configure(state="normal" if running else "disabled")
        self.cancel_btn.configure(state="normal" if running else "disabled")
        if not running:
            self.pause_btn.configure(text="Pause (finish current)")
            self.controller.pause.clear()

    def _start(self) -> None:
        if self.controller.running:
            return
        if self.tools is None:
            messagebox.showerror(APP_NAME, "ffmpeg/ffprobe not found. Open Settings and set the folder.", parent=self)
            return
        self.cfg = self._snapshot()
        save_config(self.cfg)
        out = self.cfg.output_dir.strip()
        if not out:
            messagebox.showerror(APP_NAME, "Choose an output folder.", parent=self)
            return
        output_dir = Path(out)
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Cannot create output folder:\n{exc}", parent=self)
            return

        sources = list(self.sources.values())
        if not sources:
            messagebox.showinfo(APP_NAME, "Add at least one source file.", parent=self)
            return
        if any(s.probe is None and s.status == "probing" for s in sources):
            messagebox.showinfo(APP_NAME, "Wait for probing to finish.", parent=self)
            return

        jobs = plan_jobs(
            sources=sources,
            rungs=self.cfg.rungs,
            fps_targets=self.cfg.enabled_fps(),
            output_dir=output_dir,
            overwrite=self.cfg.overwrite,
            allow_upscale=self.cfg.allow_upscale,
            copy_audio=self.cfg.copy_audio,
            log=self._log,
        )
        if not jobs:
            self._log("Nothing to encode.")
            return

        self._run_total = len(jobs)
        self._run_finished = 0
        self._file_totals.clear()
        self._file_finished.clear()
        for job in jobs:
            key = str(job.source)
            self._file_totals[key] = self._file_totals.get(key, 0) + 1
            self._file_finished[key] = 0
        for key, src in self.sources.items():
            n = self._file_totals.get(key, 0)
            if n:
                src.status = "queued"
                self._set_status(key, f"queued 0/{n}")
        self.global_bar.set(0)
        self.file_bar.set(0)
        self.global_label.configure(text=f"0 / {self._run_total} jobs")
        self.file_label.configure(text="")
        self.ffmpeg_line.configure(text="")
        self._log(f"Queue: {len(jobs)} job(s)  (files × rungs × fps)")
        self._running_controls(True)
        self.controller.start(jobs, self.tools)

    def _pause(self) -> None:
        if not self.controller.running:
            return
        if self.controller.pause.is_set():
            self.controller.request_resume()
            self.pause_btn.configure(text="Pause (finish current)")
            self._log("Resumed — next job will start.")
        else:
            self.controller.request_pause()
            self.pause_btn.configure(text="Resume")
            self._log("Pause requested — current job will finish, then wait.")

    def _cancel(self) -> None:
        if not self.controller.running:
            return
        self.controller.request_cancel()
        self._log("Cancel requested — aborting current ffmpeg process.")

    def _set_status(self, key: str, status: str) -> None:
        src = self.sources.get(key)
        if src is not None:
            src.status = status
        if self.tree.exists(key):
            self.tree.set(key, "status", status)

    def _drain_ui(self) -> None:
        try:
            while True:
                event = self.ui_queue.get_nowait()
                self._handle_event(event)
        except queue.Empty:
            pass
        if not self._closing:
            self.after(80, self._drain_ui)

    def _handle_event(self, event: tuple) -> None:
        kind = event[0]
        if kind == "probed":
            path: Path = event[1]
            info: ProbeInfo = event[2]
            key = str(path.resolve())
            src = self.sources.get(key)
            if src is None:
                return
            src.probe = info
            src.clip = compute_clip(info.duration_s, self._clip_duration(), self.clip_mode_var.get())
            src.status = "ready"
            if self.tree.exists(key):
                self.tree.item(
                    key,
                    values=(
                        path.name,
                        format_hms(info.duration_s) if info.duration_s else "unknown",
                        format_fps(info.fps),
                        info.resolution_label,
                        src.clip.label(),
                        "ready",
                    ),
                )
            self._log(
                f"Probed {path.name}: {info.resolution_label}  {format_fps(info.fps)} fps  "
                f"{info.video_codec}  {format_hms(info.duration_s)}  clip {src.clip.label()}"
            )
        elif kind == "probe_error":
            path = event[1]
            msg = event[2]
            key = str(path.resolve())
            src = self.sources.get(key)
            if src is not None:
                src.error = msg
                src.status = "error"
            self._set_status(key, "error")
            self._log(msg)
        elif kind == "job_started":
            job: EncodeJob = event[1]
            self._current_job = job
            key = str(job.source)
            done = self._file_finished.get(key, 0)
            total = self._file_totals.get(key, 1)
            self._set_status(key, f"encoding {job.rung_id} {job.fps}fps ({done}/{total})")
            self.file_label.configure(text=f"{job.source_name}  →  {job.output.name}")
            self.global_label.configure(
                text=f"{self._run_finished} / {self._run_total}  |  {job.rung_id} {job.fps}fps pass 1/2"
            )
            self._log(f"Start {job.output.name}  {job.width}x{job.height}  {job.bitrate_k}k  2-pass")
        elif kind == "job_progress":
            job = event[1]
            prog: EncodeProgress = event[2]
            self._apply_progress(job, prog)
        elif kind == "job_done":
            job = event[1]
            self._finish_job(job, "done")
            self._log(f"Wrote {job.output.name}")
        elif kind == "job_error":
            job = event[1]
            result: EncodeResult = event[2]
            self._finish_job(job, "error")
            self._log(f"ERROR {job.output.name}: {result.message}")
        elif kind == "job_cancelled":
            job = event[1]
            msg = event[2] if len(event) > 2 else "Cancelled"
            self._finish_job(job, "cancelled")
            self._log(f"Cancelled {job.output.name}: {msg}")
        elif kind == "paused":
            self.global_label.configure(text=f"Paused after current job  ({self._run_finished} / {self._run_total})")
        elif kind == "run_finished":
            stats = event[1]
            self._running_controls(False)
            self._current_job = None
            self.global_bar.set(1 if self._run_total else 0)
            self.global_label.configure(
                text=(
                    f"Finished  done={stats.done}  skipped_in_queue=0  "
                    f"failed={stats.failed}  cancelled={stats.cancelled}  total={stats.total}"
                )
            )
            self.ffmpeg_line.configure(text="")
            self._log(
                f"Run finished: {stats.done} ok, {stats.failed} failed, {stats.cancelled} cancelled "
                f"({stats.total} queued)"
            )

    def _apply_progress(self, job: EncodeJob, prog: EncodeProgress) -> None:
        job_frac = ((prog.pass_index - 1) + prog.fraction) / 2.0
        if self._run_total:
            overall = (self._run_finished + job_frac) / self._run_total
            self.global_bar.set(max(0.0, min(1.0, overall)))
        key = str(job.source)
        file_done = self._file_finished.get(key, 0)
        file_total = max(1, self._file_totals.get(key, 1))
        self.file_bar.set(max(0.0, min(1.0, (file_done + job_frac) / file_total)))
        self.ffmpeg_line.configure(text=prog.last_line[:180])
        self.global_label.configure(
            text=f"{self._run_finished} / {self._run_total}  |  {job.rung_id} {job.fps}fps  {prog.last_line}"
        )

    def _finish_job(self, job: EncodeJob, status: str) -> None:
        self._run_finished += 1
        key = str(job.source)
        self._file_finished[key] = self._file_finished.get(key, 0) + 1
        done = self._file_finished[key]
        total = self._file_totals.get(key, 1)
        if done >= total:
            self._set_status(key, status if status != "done" else "done")
            self.file_bar.set(1)
        else:
            self._set_status(key, f"{status} {done}/{total}")
        if self._run_total:
            self.global_bar.set(self._run_finished / self._run_total)

    def _open_settings(self) -> None:
        SettingsDialog(self)

    def _open_help(self) -> None:
        HelpDialog(self)

    def _on_close(self) -> None:
        self._closing = True
        if self.controller.running:
            self.controller.request_cancel()
        self._persist()
        self.destroy()


class SettingsDialog(ctk.CTkToplevel):
    def __init__(self, app: MP4ADPLadderApp) -> None:
        super().__init__(app)
        self.app = app
        self.title(f"{APP_NAME} — Settings")
        self.geometry("720x260")
        self.resizable(True, False)
        self.transient(app)
        self.grab_set()
        self.folder_var = ctk.StringVar(value=app.cfg.ffmpeg_dir)

        ctk.CTkLabel(self, text="FFmpeg folder (must contain ffmpeg.exe and ffprobe.exe)").pack(
            anchor="w", padx=16, pady=(16, 6)
        )
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=16)
        row.grid_columnconfigure(0, weight=1)
        ctk.CTkEntry(row, textvariable=self.folder_var).grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(row, text="Browse", width=90, command=self._browse).grid(row=0, column=1, padx=(8, 0))
        self.status = ctk.CTkLabel(self, text="", justify="left", anchor="w")
        self.status.pack(fill="x", padx=16, pady=12)
        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=16, pady=(0, 16))
        ctk.CTkButton(btns, text="Validate", width=110, fg_color="#3A3A3A", command=self._validate).pack(side="left")
        ctk.CTkButton(btns, text="Save", width=110, fg_color=ACCENT, command=self._save).pack(side="right")
        ctk.CTkButton(btns, text="Cancel", width=110, fg_color="#3A3A3A", command=self.destroy).pack(side="right", padx=8)
        self.after(50, self._validate)

    def _browse(self) -> None:
        folder = filedialog.askdirectory(parent=self, title="FFmpeg folder")
        if folder:
            self.folder_var.set(folder)

    def _validate(self) -> bool:
        try:
            tools = resolve_tools(self.folder_var.get().strip())
        except FFmpegError as exc:
            self.status.configure(text=str(exc), text_color="#E07070")
            return False
        self.status.configure(
            text=f"{tools.ffmpeg_version}\n{tools.ffprobe_version}",
            text_color="#8FCB7A",
        )
        return True

    def _save(self) -> None:
        if not self._validate():
            return
        self.app.cfg.ffmpeg_dir = self.folder_var.get().strip()
        save_config(self.app.cfg)
        self.app._refresh_tools()
        self.destroy()


class HelpDialog(ctk.CTkToplevel):
    def __init__(self, app: MP4ADPLadderApp) -> None:
        super().__init__(app)
        self.title(f"{APP_NAME} — Help")
        self.geometry("720x480")
        self.transient(app)
        box = ctk.CTkTextbox(self, font=ctk.CTkFont(family="Segoe UI", size=14), wrap="word")
        box.pack(fill="both", expand=True, padx=16, pady=16)
        box.insert(
            "1.0",
            HELP_TEXT.strip()
            + f"\n\nVersion {__version__}\n"
            + "Copyright (C) 2026 Adriano\n"
            + "This program is free software under the GNU GPL v3 or later. See LICENSE.",
        )
        box.configure(state="disabled")
        ctk.CTkButton(self, text="Close", width=100, command=self.destroy).pack(pady=(0, 16))


def _parse_kbps(raw: str, fallback: int) -> int:
    text = (raw or "").strip().lower().replace("kbps", "").replace("k", "").strip()
    try:
        value = int(float(text))
    except ValueError:
        return max(1, fallback)
    return max(1, value)


def _fmt_num(value: float) -> str:
    if abs(value - round(value)) < 1e-6:
        return str(int(round(value)))
    return f"{value:g}"


def run() -> None:
    _set_windows_app_id()
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("dark-blue")
    app = MP4ADPLadderApp()
    app.mainloop()
