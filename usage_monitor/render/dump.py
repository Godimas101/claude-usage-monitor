"""Frame dump: renders every theme, variant, state and scale to PNG files, with contact sheets.

dump_frames() is the offline check for the skins. It needs no window and no tray, so it runs in
CI-like environments and in the --dump-frames command of main.py. Output layout:

    <out>/<theme>/<variant>/floating_<state>_x<scale>.png
    <out>/<theme>/<variant>/taskbar_<state>_h<height>_x<scale>.png
    <out>/<theme>/<variant>/tooltip_<state>_x<scale>.png
    <out>/<theme>/<variant>/tray_<state>_<size>.png
    <out>/<theme>/contact_sheet.png      (floating, strip and tray for every variant at 1x)

Theme objects come from usage_monitor.themes.load_themes(), the one reader of the theme files.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from PIL import Image, ImageDraw

from usage_monitor import themes as theme_registry
from usage_monitor.data.models import (
    ApiStatus,
    BurnRate,
    IngestProgress,
    Limit,
    MonitorState,
    PlanInfo,
    UsageSnapshot,
)
from usage_monitor.log import get_logger
from usage_monitor.render import engine
from usage_monitor.render.model import WidgetModel, build_model
from usage_monitor.render.skins import get_skin

log = get_logger(__name__)

SCALES: tuple[float, ...] = (1.0, 1.25, 1.5, 2.0)
TASKBAR_HEIGHTS_AT_1X: tuple[int, ...] = (48, 40, 36, 32, 30)
TRAY_SIZES: tuple[int, ...] = (16, 20, 24, 32)
SAMPLE_NOW = datetime(2026, 10, 21, 20, 12, tzinfo=timezone.utc)


# ── Theme loading ─────────────────────────────────────────────────────────────

def load_theme_objects(directory: Path | None = None) -> dict[str, Any]:
    """Every shipped theme by id, as usage_monitor.themes.load_themes() returns them."""
    return dict(theme_registry.load_themes(directory))


# ── Sample states ─────────────────────────────────────────────────────────────

STATE_NAMES: tuple[str, ...] = (
    "normal3", "normal2", "warning", "critical", "stale", "offline",
    "paused", "rate_limited", "no_credentials", "loading",
)

_SAMPLE_VALUES: dict[str, tuple[float, float]] = {
    # state -> (session percent, weekly percent); fable is derived below
    "normal3": (13, 2), "normal2": (13, 2), "warning": (78, 21), "critical": (93, 58),
    "stale": (13, 2), "offline": (13, 2), "paused": (13, 2), "rate_limited": (13, 2),
}


def sample_state(name: str, now: datetime = SAMPLE_NOW) -> MonitorState | None:
    """A synthetic MonitorState for one dump state. "loading" is None (nothing published yet)."""
    if name == "loading":
        return None
    if name == "no_credentials":
        return _state(now, ApiStatus.NO_CREDENTIALS, None, 0)
    session, weekly = _SAMPLE_VALUES[name]
    limits = [
        Limit(kind="session", group="session", percent=session, severity=None,
              resets_at=now + timedelta(hours=3, minutes=27), is_active=True, label="SESSION",
              window_seconds=18000),
        Limit(kind="weekly_all", group="weekly", percent=weekly, severity=None,
              resets_at=now + timedelta(days=3, hours=0, minutes=0), is_active=True, label="WEEKLY",
              window_seconds=604800),
    ]
    if name in ("normal3", "warning", "critical"):
        fable = {"normal3": 0.0, "warning": 4.0, "critical": 12.0}[name]
        limits.append(Limit(
            kind="weekly_scoped", group="weekly", percent=fable, severity=None,
            resets_at=now + timedelta(days=3), is_active=True, label="FABLE WEEKLY",
            scope_model="Fable", window_seconds=604800, allowance_pct=50.0,
        ))
    status = {
        "normal3": ApiStatus.LIVE, "normal2": ApiStatus.LIVE, "warning": ApiStatus.LIVE,
        "critical": ApiStatus.LIVE, "stale": ApiStatus.STALE, "offline": ApiStatus.OFFLINE,
        "paused": ApiStatus.PAUSED, "rate_limited": ApiStatus.RATE_LIMITED,
    }[name]
    age = {"stale": 41 * 60, "offline": 2 * 60, "rate_limited": 6 * 60}.get(name, 12)
    return _state(now, status, limits, age)


def _state(now: datetime, status: ApiStatus, limits: list[Limit] | None, age_s: int) -> MonitorState:
    fetched = now - timedelta(seconds=age_s)
    usage = UsageSnapshot(fetched_at=fetched, limits=list(limits)) if limits is not None else None
    return MonitorState(
        status=status,
        status_detail="",
        usage=usage,
        usage_fetched_at=fetched if usage is not None else None,
        plan=PlanInfo(subscription_type="max", rate_limit_tier="default_claude_max_5x",
                      name="Max 5x", monthly_price_usd=100.0),
        windows={},
        burn=BurnRate(),
        unpriced_models=[],
        ingest=IngestProgress(),
        updated_at=now,
    )


def sample_model(name: str, now: datetime = SAMPLE_NOW, show_scoped: str = "auto") -> WidgetModel:
    return build_model(sample_state(name, now), now, show_scoped=show_scoped)  # type: ignore[arg-type]


# ── Dumping ───────────────────────────────────────────────────────────────────

@dataclass(slots=True)
class DumpReport:
    out_dir: Path
    themes: list[str]
    files: int = 0
    sheets: list[Path] = field(default_factory=list)


def dump_frames(
    out_dir: str | Path,
    themes: Iterable[str | Any] | None = None,
    *,
    variants: Iterable[str] | None = None,
    states: Iterable[str] | None = None,
    scales: Iterable[float] | None = None,
) -> DumpReport:
    """Render every theme x variant x state x scale. Returns what was written.

    themes: theme ids or loaded theme objects; None means every shipped theme.
    variants, states, scales: optional filters (ids, STATE_NAMES entries, scale factors).
    """
    out = Path(out_dir)
    loaded = load_theme_objects()
    selected = _select_themes(loaded, themes)
    variant_filter = None if variants is None else set(variants)
    state_names = STATE_NAMES if states is None else tuple(states)
    unknown = set(state_names) - set(STATE_NAMES)
    if unknown:
        raise KeyError(f"unknown dump state(s): {', '.join(sorted(unknown))}")
    scale_values = SCALES if scales is None else tuple(float(s) for s in scales)
    report = DumpReport(out_dir=out, themes=[t.id for t in selected])
    for theme in selected:
        skin = get_skin(theme.id)
        theme_dir = out / theme.id
        theme_dir.mkdir(parents=True, exist_ok=True)
        for variant in theme.variants:
            if variant_filter is not None and variant.id not in variant_filter:
                continue
            variant_dir = theme_dir / variant.id
            variant_dir.mkdir(parents=True, exist_ok=True)
            for state in state_names:
                model = sample_model(state)
                for scale in scale_values:
                    _save(skin.render_floating(model, theme, variant, scale, None),
                          variant_dir / f"floating_{state}_x{_scale_tag(scale)}.png", report)
                    for height in TASKBAR_HEIGHTS_AT_1X:
                        _save(skin.render_taskbar(model, theme, variant, round(height * scale), scale, None),
                              variant_dir / f"taskbar_{state}_h{round(height * scale)}_x{_scale_tag(scale)}.png",
                              report)
                    _save(skin.render_tooltip(model, theme, variant, scale),
                          variant_dir / f"tooltip_{state}_x{_scale_tag(scale)}.png", report)
                for size in TRAY_SIZES:
                    _save(skin.render_tray_icon(model, theme, variant, size),
                          variant_dir / f"tray_{state}_{size}.png", report)
        report.sheets.append(_contact_sheet(theme, skin, theme_dir / "contact_sheet.png", variant_filter))
    return report


def _select_themes(loaded: Mapping[str, Any], themes: Iterable[str | Any] | None) -> list[Any]:
    if themes is None:
        return [loaded[key] for key in sorted(loaded)]
    selected: list[Any] = []
    for item in themes:
        if isinstance(item, str):
            if item not in loaded:
                raise KeyError(f"unknown theme {item!r}; shipped: {', '.join(sorted(loaded))}")
            selected.append(loaded[item])
        else:
            selected.append(item)
    return selected


def _scale_tag(scale: float) -> str:
    return f"{scale:g}"


def _save(image: Image.Image, path: Path, report: DumpReport) -> None:
    image.save(path, format="PNG", optimize=False)
    report.files += 1


# ── Contact sheet ─────────────────────────────────────────────────────────────

_PAD = 14
_LABEL_H = 18
_BG = (11, 11, 11, 255)
_INK = (220, 217, 210, 255)
_MUTE = (119, 115, 107, 255)


def _contact_sheet(theme: Any, skin: Any, path: Path, variant_filter: set[str] | None = None) -> Path:
    """One row per variant: the floating widget for each state, then the strip and tray icons."""
    label_font = engine.load_font(str(engine.font_file("ibmplexmono/IBMPlexMono-Regular.ttf")), 13.0)
    rows: list[Image.Image] = []
    models = {state: sample_model(state) for state in STATE_NAMES}
    for variant in theme.variants:
        if variant_filter is not None and variant.id not in variant_filter:
            continue
        cells: list[tuple[str, Image.Image]] = []
        for state in STATE_NAMES:
            cells.append((state, skin.render_floating(models[state], theme, variant, 1.0, None)))
        row_images = [img for _, img in cells]
        widget_w = sum(img.width for img in row_images) + _PAD * (len(row_images) + 1)
        widget_h = max(img.height for img in row_images)
        strip = [(state, skin.render_taskbar(models[state], theme, variant, 48, 1.0, None))
                 for state in ("normal3", "warning", "critical", "stale")]
        tray = [skin.render_tray_icon(models["normal3"], theme, variant, size) for size in TRAY_SIZES]
        strip_w = sum(img.width for _, img in strip) + _PAD * (len(strip) + 1)
        width = max(widget_w, strip_w, 420)
        height = _LABEL_H + widget_h + _LABEL_H + 48 + _PAD * 2 + 16
        sheet = Image.new("RGBA", (width, height), _BG)
        draw = ImageDraw.Draw(sheet)
        draw.text((_PAD, 3), f"{variant.id.upper()}  {variant.name}", font=label_font, fill=_INK)
        x = _PAD
        for state, img in cells:
            sheet.alpha_composite(img, (x, _LABEL_H))
            draw.text((x, _LABEL_H + img.height + 2), state, font=label_font, fill=_MUTE)
            x += img.width + _PAD
        y = _LABEL_H + widget_h + _LABEL_H
        x = _PAD
        for state, img in strip:
            sheet.alpha_composite(img, (x, y))
            x += img.width + _PAD
        y2 = y + 48 + 6
        x = _PAD
        for size, img in zip(TRAY_SIZES, tray):
            sheet.alpha_composite(img, (x, y2))
            draw.text((x, y2 + img.height + 1), str(size), font=label_font, fill=_MUTE)
            x += img.width + _PAD
        rows.append(sheet)
    total_w = max((r.width for r in rows), default=_PAD)
    total_h = sum(r.height for r in rows) + _PAD * (len(rows) + 1)
    out = Image.new("RGBA", (total_w + _PAD * 2, total_h), _BG)
    y = _PAD
    for row in rows:
        out.alpha_composite(row, (_PAD, y))
        y += row.height + _PAD
    path.parent.mkdir(parents=True, exist_ok=True)
    out.save(path, format="PNG")
    return path
