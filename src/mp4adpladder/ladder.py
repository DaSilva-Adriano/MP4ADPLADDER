"""ABR ladder defaults: Tableau 1 midpoint GB/h → bitrate."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

# Mbps = GB/h * 8 / 3.6  →  kbps = round(Mbps * 1000)
# Midpoints are the Tableau 1 streaming GB/h band centers.


@dataclass(frozen=True)
class RungDefault:
    id: str
    width: int
    height: int
    gb_h_min: float
    gb_h_max: float
    gb_h_mid: float
    bitrate_k: int


RUNG_DEFAULTS: tuple[RungDefault, ...] = (
    RungDefault("360p", 640, 360, 0.2, 0.5, 0.35, 778),
    RungDefault("480p", 854, 480, 0.5, 1.0, 0.75, 1667),
    RungDefault("720p", 1280, 720, 1.0, 2.5, 1.75, 3890),
    RungDefault("1080p", 1920, 1080, 2.5, 4.0, 3.25, 7222),
    RungDefault("4k", 3840, 2160, 8.0, 15.0, 11.5, 25556),
)

FPS_CHOICES: tuple[int, ...] = (24, 30, 50, 60)


def gb_h_to_kbps(gb_h: float) -> int:
    """Convert streaming GB/h to integer kbps (Mbps = GB/h * 8 / 3.6)."""
    mbps = gb_h * 8.0 / 3.6
    return int(round(mbps * 1000.0))


def kbps_to_gb_h(bitrate_k: float) -> float:
    """Inverse of gb_h_to_kbps: GB/h = Mbps * 3.6 / 8."""
    mbps = float(bitrate_k) / 1000.0
    return mbps * 3.6 / 8.0


def format_gb_h(bitrate_k: float) -> str:
    value = kbps_to_gb_h(bitrate_k)
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return f"{text} GB/h"


def even_dim(value: int) -> int:
    return value if value % 2 == 0 else value - 1


@dataclass
class RungState:
    id: str
    width: int
    height: int
    gb_h_min: float
    gb_h_max: float
    bitrate_k: int
    enabled: bool = True

    @property
    def size_label(self) -> str:
        return f"{self.width}x{self.height}"

    @property
    def gb_h_hint(self) -> str:
        return f"{self.gb_h_min:g}–{self.gb_h_max:g}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any], fallback: RungDefault) -> RungState:
        return cls(
            id=str(data.get("id", fallback.id)),
            width=int(data.get("width", fallback.width)),
            height=int(data.get("height", fallback.height)),
            gb_h_min=float(data.get("gb_h_min", fallback.gb_h_min)),
            gb_h_max=float(data.get("gb_h_max", fallback.gb_h_max)),
            bitrate_k=int(data.get("bitrate_k", fallback.bitrate_k)),
            enabled=bool(data.get("enabled", True)),
        )


def default_rungs() -> list[RungState]:
    return [
        RungState(
            id=d.id,
            width=d.width,
            height=d.height,
            gb_h_min=d.gb_h_min,
            gb_h_max=d.gb_h_max,
            bitrate_k=d.bitrate_k,
            enabled=True,
        )
        for d in RUNG_DEFAULTS
    ]


def rungs_from_config(raw: Any) -> list[RungState]:
    by_id = {d.id: d for d in RUNG_DEFAULTS}
    if not isinstance(raw, list) or not raw:
        return default_rungs()
    out: list[RungState] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        rid = str(item.get("id", ""))
        fallback = by_id.get(rid)
        if fallback is None:
            continue
        out.append(RungState.from_dict(item, fallback))
        seen.add(rid)
    for d in RUNG_DEFAULTS:
        if d.id not in seen:
            out.append(
                RungState(
                    id=d.id,
                    width=d.width,
                    height=d.height,
                    gb_h_min=d.gb_h_min,
                    gb_h_max=d.gb_h_max,
                    bitrate_k=d.bitrate_k,
                    enabled=True,
                )
            )
    order = {d.id: i for i, d in enumerate(RUNG_DEFAULTS)}
    out.sort(key=lambda r: order.get(r.id, 99))
    return out
