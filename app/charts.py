"""Server-rendered SVG line charts.

Datastar morphs HTML sent from the server, so charts are plain SVG markup that gets
re-sent with each update: no client-side chart library, nothing for the morph to wipe.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from zoneinfo import ZoneInfo

from markupsafe import Markup, escape

W, H = 640, 180
PAD_L, PAD_R, PAD_T, PAD_B = 46, 8, 10, 22


def line_chart(
    points: Sequence[tuple[dt.datetime, float | None]],
    *,
    start: dt.datetime,
    end: dt.datetime,
    y_min: float,
    y_max: float,
    tz: ZoneInfo,
    css_class: str,
    label: str,
    thresholds: Sequence[float] = (),
    max_gap: dt.timedelta | None = None,
) -> Markup:
    """Render a time series. `points`, `start` and `end` are naive UTC datetimes.

    The line breaks wherever two consecutive points are more than `max_gap` apart, so
    periods when the device was offline show as gaps instead of straight interpolation.
    """
    span = (end - start).total_seconds() or 1.0
    y_span = (y_max - y_min) or 1.0
    plot_w, plot_h = W - PAD_L - PAD_R, H - PAD_T - PAD_B

    def x(t: dt.datetime) -> float:
        return PAD_L + plot_w * (t - start).total_seconds() / span

    def y(v: float) -> float:
        v = min(max(v, y_min), y_max)
        return PAD_T + plot_h * (1 - (v - y_min) / y_span)

    # Split into continuous segments.
    segments: list[list[tuple[float, float]]] = []
    prev_t: dt.datetime | None = None
    for t, v in points:
        if v is None:
            prev_t = None
            continue
        if prev_t is None or (max_gap is not None and t - prev_t > max_gap):
            segments.append([])
        segments[-1].append((x(t), y(v)))
        prev_t = t

    parts: list[str] = [
        f'<svg class="chart {escape(css_class)}" viewBox="0 0 {W} {H}" role="img" '
        f'aria-label="{escape(label)}">'
    ]

    # Horizontal grid + y labels.
    for frac in (0, 0.5, 1):
        v = y_min + y_span * frac
        gy = y(v)
        parts.append(f'<line class="grid" x1="{PAD_L}" x2="{W - PAD_R}" y1="{gy:.1f}" y2="{gy:.1f}"/>')
        parts.append(f'<text class="axis" x="{PAD_L - 6}" y="{gy + 4:.1f}" text-anchor="end">{v:.0f}</text>')

    for v in thresholds:
        ty = y(v)
        parts.append(
            f'<line class="threshold" x1="{PAD_L}" x2="{W - PAD_R}" y1="{ty:.1f}" y2="{ty:.1f}"/>'
        )

    # X labels: start, middle, end in local time.
    for frac, anchor in ((0, "start"), (0.5, "middle"), (1, "end")):
        t = start + (end - start) * frac
        local = t.replace(tzinfo=dt.UTC).astimezone(tz)
        parts.append(
            f'<text class="axis" x="{PAD_L + plot_w * frac:.1f}" y="{H - 6}" '
            f'text-anchor="{anchor}">{local:%H:%M}</text>'
        )

    base = PAD_T + plot_h
    for seg in segments:
        if len(seg) == 1:
            px, py = seg[0]
            parts.append(f'<circle class="dot" cx="{px:.1f}" cy="{py:.1f}" r="2.5"/>')
            continue
        pts = " ".join(f"{px:.1f},{py:.1f}" for px, py in seg)
        area = f"{seg[0][0]:.1f},{base} {pts} {seg[-1][0]:.1f},{base}"
        parts.append(f'<polygon class="area" points="{area}"/>')
        parts.append(f'<polyline class="line" points="{pts}"/>')

    if not segments:
        parts.append(
            f'<text class="empty" x="{W / 2}" y="{H / 2}" text-anchor="middle">No data yet</text>'
        )

    parts.append("</svg>")
    return Markup("".join(parts))
