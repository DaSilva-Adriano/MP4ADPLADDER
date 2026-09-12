# MP4ADPLADDER

Windows desktop app that batch-encodes an **x265 / MP4 ABR ladder** from selected sources.

## Run

Python 3.12+ and [uv](https://docs.astral.sh/uv/). ffmpeg / ffprobe default to:

`C:\VSR\ffmpeg-9.0.1-full_build\bin`

```bat
uv sync
uv run python -m mp4adpladder
```

Or double-click `run.cmd` (cmd, not PowerShell — Windows often blocks `.ps1`).

Window title, executable display name, settings folder, and project name: **MP4ADPLADDER**. Package module: `mp4adpladder`.

Settings persist in `%APPDATA%\MP4ADPLADDER\config.json`. See `sample-config.json` for the schema. The ffmpeg folder can be changed in **Settings**.

## Ladder (defaults)

GB/h = `(kbps / 1000) * 3.6 / 8`. The UI shows GB/h from the target kbps (`-b:v`).

| id    | size      | default `-b:v` | GB/h |
|-------|-----------|----------------|------|
| 360p  | 640×360   | 778k           | 0.35 |
| 480p  | 854×480   | 1667k          | 0.75 |
| 720p  | 1280×720  | 3890k          | 1.75 |
| 1080p | 1920×1080 | 7222k          | 3.25 |
| 4k    | 3840×2160 | 25556k         | 11.5 |

Each rung is `source → that resolution` with `scale=W:H:flags=lanczos` (even dims). There is **no** intermediate 4K transcode. Rungs larger than the source are skipped unless **Allow upscale** is on (default off).

## Encode

- `libx265`, `yuv420p`, Main, `-tag:v hvc1`, `+faststart`
- Video only (`-an`) unless **Copy audio**: AAC 128k, or `-c:a copy` if the source is already AAC
- `-preset medium`
- **ABR** (default): 2-pass x265 (`pass=1` to `NUL`, `pass=2` to file), unique stats file per job, `-maxrate 1.25*b:v`, `-bufsize 2*b:v`
- **CRF**: single-pass `-crf` (default **18**). Same CRF on every enabled rung. The `adp` token in the filename is replaced by `crf`.

## FPS

Default **24**. Checkboxes: 24, 30, 50, 60.

A target fps is used only if `source_fps >= target - 0.5`. Never up-convert. A 23.976 source may output 24. Checking 60 on a 24 fps source skips 60 and logs why.

One output file per `(source × enabled rung × allowed fps)`.

## Clip window

Default **10 s**, radio **Middle** (also Start / End).

- Middle: `start = max(0, duration/2 - T/2)`. If the source is shorter than T, the full source is used.
- Start: `-ss 0 -t T`
- End: `start = max(0, source_dur - T)`

Seek: fast input `-ss` (before `-i`) with a 10 s preroll, then an accurate post-input `-ss`. That covers keyframe drift above 0.25 s without decoding the whole file. Computed in/out timecodes are shown per file after probe.

## Inputs / outputs

Multi-select files and **Add folder** (optional recursive). Extensions: `mkv, mp4, mov, m4v, webm, avi, mxf, hevc, y4m`.

Default output dir: `./output` next to the first source (browsable).

```
{stem}_adp-{rung}-{fps}fps.mp4
{stem}_crf-{rung}-{fps}fps.mp4
film_adp-720p-24fps.mp4
film_adp-4k-24fps.mp4
film_crf-720p-24fps.mp4
film_crf-4k-24fps.mp4
```

Existing files are skipped unless **Overwrite** is checked.

## Probe

`ffprobe` JSON: duration, width, height, `avg_frame_rate`, codec. Audio-only files are rejected.

## UI

Start / Pause (finish current job) / Cancel. Global and per-file progress plus the current ffmpeg line (fps, speed, pass 1/2). Encode jobs run on a worker thread so the window does not freeze.

## License

[GNU General Public License v3.0](LICENSE) (GPL-3.0-or-later).

Copyright (C) 2026 Adriano.
