"""Tiny server-rendered SVG charts for the webapp — no JS charting library,
consistent with the rest of this app (plain Flask + Jinja, no frontend
build step). Follows the house dataviz rules: one hue for a single
magnitude-over-time series (no legend needed — the card title names it),
recessive gridlines, a native `<title>` tooltip per point instead of a JS
hover layer.
"""
from __future__ import annotations

from datetime import datetime

_LINE_COLOR = "#f60100"  # matches --flowbix-red in style.css
_GRID_COLOR = "#eeeeee"
_LABEL_COLOR = "#666666"


def render_growth_chart(samples: list[dict], width: int = 640, height: int = 220) -> str | None:
    """`samples`: chronological list of {"timestamp": epoch_seconds, "total_gb": float},
    as stored by `ClientStore.append_size_history`. Returns an inline `<svg>`
    markup string, or None if there aren't enough points to draw a trend
    (mirrors the >=2-samples threshold the rule engine itself uses)."""
    if len(samples) < 2:
        return None

    pad_left, pad_right, pad_top, pad_bottom = 48, 16, 16, 28
    plot_w = width - pad_left - pad_right
    plot_h = height - pad_top - pad_bottom

    xs = [s["timestamp"] for s in samples]
    ys = [s["total_gb"] for s in samples]
    x_min, x_max = min(xs), max(xs)
    y_min = 0.0
    y_max = max(ys) * 1.15 or 1.0  # 15% headroom so the line never touches the top

    def x_px(x):
        if x_max == x_min:
            return pad_left + plot_w / 2
        return pad_left + (x - x_min) / (x_max - x_min) * plot_w

    def y_px(y):
        return pad_top + plot_h - (y - y_min) / (y_max - y_min) * plot_h

    grid = []
    for i in range(4):
        gy = pad_top + plot_h * i / 3
        label_val = y_max - (y_max - y_min) * i / 3
        grid.append(
            f'<line x1="{pad_left}" y1="{gy:.1f}" x2="{pad_left + plot_w}" y2="{gy:.1f}" '
            f'stroke="{_GRID_COLOR}" stroke-width="1"/>'
        )
        grid.append(
            f'<text x="{pad_left - 8}" y="{gy + 4:.1f}" font-size="10" fill="{_LABEL_COLOR}" '
            f'text-anchor="end">{label_val:.1f}</text>'
        )

    points = [(x_px(s["timestamp"]), y_px(s["total_gb"])) for s in samples]
    path_d = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in points)

    circles = []
    for sample, (x, y) in zip(samples, points):
        date_label = datetime.fromtimestamp(sample["timestamp"]).strftime("%d/%m/%Y")
        circles.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{_LINE_COLOR}">'
            f'<title>{date_label}: {sample["total_gb"]:.2f} GB</title></circle>'
        )

    first_label = datetime.fromtimestamp(samples[0]["timestamp"]).strftime("%d/%m/%Y")
    last_label = datetime.fromtimestamp(samples[-1]["timestamp"]).strftime("%d/%m/%Y")

    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" '
        f'role="img" aria-label="Crescimento do banco de dados ao longo do tempo">'
        f'{"".join(grid)}'
        f'<path d="{path_d}" fill="none" stroke="{_LINE_COLOR}" stroke-width="2" '
        f'stroke-linecap="round" stroke-linejoin="round"/>'
        f'{"".join(circles)}'
        f'<text x="{pad_left}" y="{height - 6}" font-size="10" fill="{_LABEL_COLOR}">{first_label}</text>'
        f'<text x="{pad_left + plot_w}" y="{height - 6}" font-size="10" fill="{_LABEL_COLOR}" '
        f'text-anchor="end">{last_label}</text>'
        f'</svg>'
    )
