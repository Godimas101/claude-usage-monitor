"""Tests for usage_monitor.render: the view model, the Pillow toolkit, the Nostromo skin and the dump.

Everything renders to memory or to pytest's tmp_path. No window, tray icon or process is created,
so none of these tests needs CUM_UI_TESTS=1 and all of them run in CI.
"""
from __future__ import annotations

import time
from dataclasses import replace
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from usage_monitor.data.models import (
    ApiStatus,
    BurnRate,
    IngestProgress,
    Limit,
    MonitorState,
    PlanInfo,
    UsageSnapshot,
)
from usage_monitor.render import dump, engine
from usage_monitor.render.model import (
    SEVERITY_CRITICAL,
    SEVERITY_NORMAL,
    SEVERITY_WARNING,
    STALE_MESSAGE_EXPIRED,
    STALE_MESSAGE_NO_LOGIN,
    build_model,
    elapsed_fraction,
    format_age,
    format_resets,
    pace_for,
    severity_for,
    share_text,
    worst_severity,
)
from usage_monitor.render.skins import RenderOptions, coerce_options, get_skin
from usage_monitor.render.skins.nostromo import NostromoSkin, Palette

# A fixed local clock: Wednesday 2026-10-21 16:12, the sample clock used across the mockups.
NOW = datetime(2026, 10, 21, 16, 12).astimezone()
PLAN = PlanInfo(subscription_type="max", rate_limit_tier="default_claude_max_5x", name="Max 5x",
                monthly_price_usd=100.0)
ALL_STATES = dump.STATE_NAMES
SCALES = (1.0, 1.25, 1.5, 2.0)
FLOAT_HEIGHTS = {
    "normal3": 126, "normal2": 94, "warning": 126, "critical": 126, "stale": 126,
    "offline": 94, "paused": 94, "rate_limited": 94, "no_credentials": 126, "loading": 94,
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def limit(kind: str, percent: float, *, resets_in: timedelta | None = timedelta(hours=2), label: str | None = None,
          severity: str | None = None, active: bool = True, window: int | None = None,
          scope: str | None = None, allowance: float | None = None) -> Limit:
    resets_at = None if resets_in is None else NOW + resets_in
    default_label = {"session": "SESSION", "weekly_all": "WEEKLY"}.get(kind, "FABLE WEEKLY")
    return Limit(
        kind=kind,
        group="session" if kind == "session" else "weekly",
        percent=percent,
        severity=severity,
        resets_at=resets_at,
        is_active=active,
        label=label or default_label,
        scope_model=scope,
        window_seconds=window,
        allowance_pct=allowance,
    )


def state(*limits: Limit, status: ApiStatus = ApiStatus.LIVE, age: timedelta = timedelta(seconds=12),
          with_usage: bool = True) -> MonitorState:
    fetched = NOW - age
    usage = UsageSnapshot(fetched_at=fetched, limits=list(limits)) if with_usage else None
    return MonitorState(
        status=status,
        status_detail="",
        usage=usage,
        usage_fetched_at=fetched if usage is not None else None,
        plan=PLAN,
        windows={},
        burn=BurnRate(),
        unpriced_models=[],
        ingest=IngestProgress(),
        updated_at=NOW,
    )


@lru_cache(maxsize=1)
def themes() -> dict[str, Any]:
    return dump.load_theme_objects()


def nostromo():
    return themes()["nostromo"]


def alpha_extrema(image: Image.Image) -> tuple[int, int]:
    return image.getchannel("A").getextrema()


# ── View model: status, staleness and messages ───────────────────────────────

def test_missing_state_renders_as_loading_with_dashes():
    model = build_model(None, NOW)
    assert model.status is ApiStatus.LOADING
    assert model.status_text == "LOADING"
    assert model.stale is True
    assert model.stale_message is None
    assert model.age_text is None
    assert model.updated_at == NOW
    assert [row.key for row in model.rows] == ["session", "weekly"]
    assert all(row.percent is None and row.time_fraction is None for row in model.rows)


@pytest.mark.parametrize(("status", "text"), [
    (ApiStatus.LOADING, "LOADING"),
    (ApiStatus.LIVE, "LIVE"),
    (ApiStatus.STALE, "STALE"),
    (ApiStatus.OFFLINE, "OFFLINE"),
    (ApiStatus.RATE_LIMITED, "RATE LIMITED"),
    (ApiStatus.NO_CREDENTIALS, "NO LOGIN"),
    (ApiStatus.PAUSED, "PAUSED"),
])
def test_status_text_for_every_status(status, text):
    model = build_model(state(limit("session", 10), limit("weekly_all", 5), status=status), NOW)
    assert model.status_text == text


@pytest.mark.parametrize(("status", "stale", "message"), [
    (ApiStatus.LIVE, False, None),
    (ApiStatus.PAUSED, False, None),
    (ApiStatus.STALE, True, STALE_MESSAGE_EXPIRED),
    (ApiStatus.NO_CREDENTIALS, True, STALE_MESSAGE_NO_LOGIN),
    (ApiStatus.OFFLINE, True, None),
    (ApiStatus.RATE_LIMITED, True, None),
])
def test_stale_flag_and_message_per_status(status, stale, message):
    model = build_model(state(limit("session", 10), limit("weekly_all", 5), status=status), NOW)
    assert model.stale is stale
    assert model.stale_message == message


def test_expired_login_message_text_is_the_agreed_wording():
    assert STALE_MESSAGE_EXPIRED == "LOGIN EXPIRED - OPEN CLAUDE CODE"


def test_no_usage_keeps_status_but_dashes_the_rows():
    model = build_model(state(status=ApiStatus.OFFLINE, with_usage=False), NOW)
    assert model.status_text == "OFFLINE"
    assert model.age_text is None
    assert [row.percent for row in model.rows] == [None, None]


def test_blink_flag_passes_through():
    assert build_model(state(limit("session", 1)), NOW, blink_on=False).blink_on is False
    assert build_model(state(limit("session", 1)), NOW).blink_on is True


# ── View model: severity ──────────────────────────────────────────────────────

@pytest.mark.parametrize(("api", "percent", "expected"), [
    ("normal", 99.0, SEVERITY_NORMAL),
    ("NORMAL", 99.0, SEVERITY_NORMAL),
    ("warning", 3.0, SEVERITY_WARNING),
    ("critical", 3.0, SEVERITY_CRITICAL),
    ("exceeded", 3.0, SEVERITY_CRITICAL),       # anything worse than warning is critical
])
def test_api_severity_wins_over_percent(api, percent, expected):
    assert severity_for(api, percent) == expected


@pytest.mark.parametrize(("percent", "expected"), [
    (0.0, SEVERITY_NORMAL),
    (74.9, SEVERITY_NORMAL),
    (75.0, SEVERITY_WARNING),
    (89.9, SEVERITY_WARNING),
    (90.0, SEVERITY_CRITICAL),
    (112.0, SEVERITY_CRITICAL),
])
def test_threshold_severity_when_api_is_silent(percent, expected):
    assert severity_for(None, percent) == expected
    assert severity_for("  ", percent) == expected


def test_severity_of_missing_percent_is_normal():
    assert severity_for(None, None) == SEVERITY_NORMAL


def test_worst_severity_ignores_rows_without_data():
    model = build_model(state(limit("session", 95), limit("weekly_all", 10)), NOW)
    assert worst_severity(model.rows) == SEVERITY_CRITICAL
    empty = build_model(state(status=ApiStatus.OFFLINE, with_usage=False), NOW)
    assert worst_severity(empty.rows) == SEVERITY_NORMAL


# ── View model: time windows and pace ─────────────────────────────────────────

def test_time_fraction_uses_default_windows():
    session = limit("session", 10, resets_in=timedelta(hours=3, minutes=27))   # 31 % elapsed
    weekly = limit("weekly_all", 10, resets_in=timedelta(days=3))              # 4/7 elapsed
    model = build_model(state(session, weekly), NOW)
    assert model.rows[0].time_fraction == pytest.approx(0.31, abs=0.001)
    assert model.rows[1].time_fraction == pytest.approx(4 / 7, abs=0.001)


def test_time_fraction_prefers_the_window_seconds_the_api_gives():
    one_hour = limit("session", 10, resets_in=timedelta(minutes=45), window=3600)
    model = build_model(state(one_hour), NOW)
    assert model.rows[0].time_fraction == pytest.approx(0.25, abs=0.001)


def test_time_fraction_is_clamped_and_none_without_reset():
    overdue = limit("session", 10, resets_in=timedelta(hours=-1))
    far = limit("session", 10, resets_in=timedelta(days=30))
    unknown = limit("session", 10, resets_in=None)
    assert build_model(state(overdue), NOW).rows[0].time_fraction == 1.0
    assert build_model(state(far), NOW).rows[0].time_fraction == 0.0
    assert build_model(state(unknown), NOW).rows[0].time_fraction is None


def test_elapsed_fraction_rejects_non_positive_windows():
    assert elapsed_fraction(NOW, 0, NOW) is None
    assert elapsed_fraction(None, 18000, NOW) is None


@pytest.mark.parametrize(("percent", "fraction", "expected"), [
    (70.0, 0.5, "ahead"),        # +20 points
    (30.0, 0.5, "behind"),       # -20 points
    (50.0, 0.5, "on_pace"),
    (60.0, 0.5, "on_pace"),      # exactly +10 is still on pace
    (60.1, 0.5, "ahead"),
    (40.0, 0.5, "on_pace"),     # exactly -10 is still on pace
    (39.9, 0.5, "behind"),      # -10.1 points
    (50.0, None, None),
    (None, 0.5, None),
])
def test_pace_bands(percent, fraction, expected):
    assert pace_for(percent, fraction) == expected


def test_pace_in_the_built_rows():
    ahead = build_model(state(limit("session", 72, resets_in=timedelta(hours=3, minutes=27))), NOW)
    assert ahead.rows[0].pace == "ahead"
    behind = build_model(state(limit("session", 5, resets_in=timedelta(hours=3, minutes=27)),
                               limit("weekly_all", 5, resets_in=timedelta(days=3))), NOW)
    assert behind.rows[0].pace == "behind"
    assert behind.rows[1].pace == "behind"


# ── View model: local reset text ──────────────────────────────────────────────

def test_resets_text_same_local_day_is_clock_only():
    reset = datetime(2026, 10, 21, 19, 39).astimezone()
    assert format_resets(reset, NOW) == "7:39 PM"


def test_resets_text_other_day_adds_weekday():
    reset = datetime(2026, 10, 24, 16, 0).astimezone()
    assert format_resets(reset, NOW) == "Sat 4:00 PM"


def test_resets_text_midnight_and_noon_use_twelve_hour_clock():
    assert format_resets(datetime(2026, 10, 21, 0, 5).astimezone(), NOW) == "12:05 AM"
    assert format_resets(datetime(2026, 10, 21, 12, 30).astimezone(), NOW) == "12:30 PM"


def test_resets_text_in_the_built_row_is_local():
    reset = datetime(2026, 10, 21, 19, 39).astimezone()
    model = build_model(state(limit("session", 10, resets_in=reset - NOW)), NOW)
    assert model.rows[0].resets_text == "7:39 PM"
    assert model.rows[0].resets_at == reset


# ── View model: scoped rows ───────────────────────────────────────────────────

def scoped(percent: float, *, active: bool, allowance: float | None = 50.0) -> Limit:
    return limit("weekly_scoped", percent, label="FABLE WEEKLY", active=active, scope="Fable",
                 allowance=allowance, resets_in=timedelta(days=3))


@pytest.mark.parametrize(("percent", "active", "mode", "shown"), [
    (0.0, False, "auto", False),     # idle scoped row hidden in auto
    (0.5, False, "auto", False),     # below 1 % and not active
    (0.5, True, "auto", True),       # active shows in auto
    (1.0, False, "auto", True),      # 1 % shows in auto
    (0.0, False, "always", True),
    (50.0, True, "never", False),
])
def test_scoped_row_visibility(percent, active, mode, shown):
    model = build_model(state(limit("session", 10), limit("weekly_all", 10), scoped(percent, active=active)),
                        NOW, show_scoped=mode)
    keys = [row.key for row in model.rows]
    assert ("scoped:fable" in keys) is shown


def test_scoped_row_carries_share_text_and_label():
    model = build_model(state(limit("session", 10), limit("weekly_all", 10), scoped(4.0, active=True)), NOW)
    row = next(r for r in model.rows if r.key == "scoped:fable")
    assert row.label == "FABLE WEEKLY"
    assert row.share_text == "50% SHARE"
    assert share_text(None) is None
    assert share_text(33.4) == "33% SHARE"


def test_invalid_show_scoped_mode_is_rejected():
    with pytest.raises(ValueError):
        build_model(state(limit("session", 10)), NOW, show_scoped="sometimes")  # type: ignore[arg-type]


# ── View model: age text ──────────────────────────────────────────────────────

@pytest.mark.parametrize(("seconds", "text"), [
    (0, "0 S AGO"),
    (12, "12 S AGO"),
    (59, "59 S AGO"),
    (60, "1 M AGO"),
    (41 * 60, "41 M AGO"),
    (3 * 3600, "3 H AGO"),
    (47 * 3600, "47 H AGO"),
    (50 * 3600, "2 D AGO"),
    (-30, "0 S AGO"),
])
def test_age_text(seconds, text):
    assert format_age(NOW - timedelta(seconds=seconds), NOW) == text


def test_age_text_in_the_built_model():
    model = build_model(state(limit("session", 10), age=timedelta(seconds=12)), NOW)
    assert model.age_text == "12 S AGO"


def test_row_keys_and_labels_for_the_basic_pair():
    model = build_model(state(limit("session", 10), limit("weekly_all", 20)), NOW)
    assert [(r.key, r.label) for r in model.rows] == [("session", "SESSION"), ("weekly", "WEEKLY")]
    assert [r.percent for r in model.rows] == [10.0, 20.0]


def test_elapsed_time_is_never_negative_for_future_fetch():
    model = build_model(state(limit("session", 10), age=timedelta(seconds=-20)), NOW)
    assert model.age_text == "0 S AGO"


# ── Engine ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("text", "rgba"), [
    ("#abc", (0xAA, 0xBB, 0xCC, 255)),
    ("#112233", (0x11, 0x22, 0x33, 255)),
    ("#11223344", (0x11, 0x22, 0x33, 0x44)),
    ("#e8a02040", (0xE8, 0xA0, 0x20, 0x40)),
])
def test_parse_color_forms(text, rgba):
    assert engine.parse_color(text) == rgba


@pytest.mark.parametrize("text", ["abc", "#12345", "#zzzzzz", "#1234567", ""])
def test_parse_color_rejects_bad_input(text):
    with pytest.raises(ValueError):
        engine.parse_color(text)


def test_canvas_output_size_follows_scale():
    assert engine.Canvas(280, 126, scale=1.0).finish().size == (280, 126)
    assert engine.Canvas(280, 126, scale=2.0).finish().size == (560, 252)
    assert engine.Canvas(10, 4, scale=1.25).finish().size == (12, 5)


def test_canvas_rectangles_are_exact_before_downsampling():
    # Checked on the 2x buffer: LANCZOS rings at edges, so the exact geometry is read before it.
    canvas = engine.Canvas(10, 4, scale=1.0)
    canvas.fill_rect(2, 1, 5, 3, (255, 0, 0, 255))
    buffer = canvas.image
    assert buffer.getpixel((4, 2)) == (255, 0, 0, 255)
    assert buffer.getpixel((9, 5)) == (255, 0, 0, 255)
    assert buffer.getpixel((10, 5)) == (0, 0, 0, 0)
    assert buffer.getpixel((3, 2)) == (0, 0, 0, 0)


def test_polygon_far_edges_are_exclusive():
    canvas = engine.Canvas(10, 10, scale=1.0)
    canvas.fill_polygon([(2, 2), (6, 2), (6, 6), (2, 6)], (0, 255, 0, 255))
    buffer = canvas.image
    assert buffer.getpixel((4, 4)) == (0, 255, 0, 255)
    assert buffer.getpixel((11, 11)) == (0, 255, 0, 255)
    assert buffer.getpixel((12, 11))[3] == 0
    assert buffer.getpixel((11, 12))[3] == 0


def test_translucent_fill_keeps_straight_alpha():
    canvas = engine.Canvas(4, 4, scale=1.0)
    canvas.fill_rect(0, 0, 4, 4, (255, 255, 255, 128))
    pixel = canvas.finish().getpixel((1, 1))
    assert pixel[:3] == (255, 255, 255)           # colour not darkened by the transparent surround
    assert pixel[3] == 128


def test_dotted_rail_pattern():
    canvas = engine.Canvas(16, 2, scale=1.0)
    canvas.dotted_hline(0, 16, 0, 2, 2, (255, 255, 255, 255))
    row = [canvas.image.getpixel((x, 0))[3] for x in range(32)]
    # 2 px on and 2 px off in logical pixels: 4 buffer px lit, 4 dark, repeating.
    assert row[0:4] == [255] * 4
    assert row[4:8] == [0] * 4
    assert row[8:12] == [255] * 4


def test_rounded_rect_zero_radius_is_plain_rectangle():
    canvas = engine.Canvas(8, 8, scale=1.0)
    layer = canvas.rounded_rect_layer(0, 0, 8, 8, 0)
    assert layer.mask.getextrema() == (255, 255)


def test_split_segments_fills_cells_from_the_left():
    assert engine.split_segments(None, 16) == (0, 0.0)
    assert engine.split_segments(0.0, 16) == (0, 0.0)
    assert engine.split_segments(100.0, 16) == (16, 0.0)
    full, fraction = engine.split_segments(13.0, 16)
    assert full == 2
    assert fraction == pytest.approx(0.08, abs=0.001)
    assert engine.split_segments(150.0, 16) == (16, 0.0)


def test_chamfer_inset_matches_the_css_value():
    body = engine.inset_chamfer(0, 0, 280, 126, 10, 10, 1)
    assert body[0][0] == pytest.approx(10.414, abs=0.01)
    assert body[5][1] == pytest.approx(10.414, abs=0.01)


def test_bgra_byte_helpers_order_and_premultiplication():
    pixel = Image.new("RGBA", (1, 1), (255, 0, 0, 128))
    assert engine.to_bgra_straight(pixel) == bytes((0, 0, 255, 128))
    assert engine.to_bgra_premultiplied(pixel) == bytes((0, 0, 128, 128))
    frame = Image.new("RGBA", (5, 3), (10, 20, 30, 40))
    assert len(engine.to_bgra_straight(frame)) == 5 * 3 * 4
    assert len(engine.to_bgra_premultiplied(frame)) == 5 * 3 * 4


def test_font_cache_returns_the_same_face_for_the_same_key():
    path = str(engine.font_file("vt323/VT323-Regular.ttf"))
    assert engine.load_font(path, 22.0) is engine.load_font(path, 22.0)


def test_bundled_fonts_are_present():
    for relative in ("vt323/VT323-Regular.ttf", "ibmplexmono/IBMPlexMono-SemiBold.ttf",
                     "ibmplexmono/IBMPlexMono-Regular.ttf"):
        assert engine.font_file(relative).is_file(), relative


def test_vignette_darkens_corners_not_centre():
    canvas = engine.Canvas(40, 40, scale=1.0)
    canvas.fill_rect(0, 0, 40, 40, (200, 200, 200, 255))
    canvas.vignette(1.0)
    image = canvas.finish()
    assert image.getpixel((20, 20))[0] > image.getpixel((1, 1))[0]


def test_hazard_stripes_stay_inside_the_polygon():
    canvas = engine.Canvas(40, 10, scale=1.0)
    outline = engine.chamfer_polygon(0, 0, 40, 10, 4, 4)
    canvas.hazard(outline, 0, 40, 0, 4, 4, (255, 179, 0, 255), (9, 9, 9, 255))
    buffer = canvas.image
    assert buffer.getpixel((0, 0))[3] == 0       # the chamfer corner stays clear
    assert buffer.getpixel((60, 2))[3] == 255    # the band is opaque inside the outline
    assert buffer.getpixel((60, 12))[3] == 0     # and stops at its 4 px height


# ── Options ───────────────────────────────────────────────────────────────────

def test_coerce_options_accepts_none_mapping_and_object():
    assert coerce_options(None) == RenderOptions()
    assert coerce_options({"text_fx": False}).text_fx is False
    custom = RenderOptions(transparent_bg=True)
    assert coerce_options(custom) is custom


def test_coerce_options_rejects_unknown_names():
    with pytest.raises(ValueError):
        coerce_options({"glow": True})
    with pytest.raises(TypeError):
        coerce_options(42)  # type: ignore[arg-type]


# ── Skin registry ─────────────────────────────────────────────────────────────

def test_get_skin_returns_nostromo_and_falls_back_to_it():
    assert isinstance(get_skin("nostromo"), NostromoSkin)
    assert isinstance(get_skin("night_city"), NostromoSkin)
    assert isinstance(get_skin("gates"), NostromoSkin)
    assert isinstance(get_skin(None), NostromoSkin)
    assert isinstance(get_skin("no-such-theme"), NostromoSkin)


# ── Palette and tokens ────────────────────────────────────────────────────────

@pytest.mark.parametrize("variant_id", ["amber", "green", "white", "red", "blue", "cyan", "orange", "purple"])
def test_palette_tokens_come_from_the_variant(variant_id):
    theme = nostromo()
    variant = theme.variant(variant_id)
    palette = Palette.for_variant(theme, variant)
    colors = variant.colors
    assert palette.crt == engine.parse_color(colors["bg"])
    assert palette.hot == engine.parse_color(colors["text_bright"])
    assert palette.main == engine.parse_color(colors["text"])
    assert palette.bright == engine.parse_color(colors["accent"])
    assert palette.label == engine.parse_color(colors["text_dim"])
    assert palette.ghost == engine.parse_color(colors["track"])
    assert palette.rule == engine.parse_color(colors["border_dim"])
    assert palette.glow == engine.parse_color(colors["glow"])
    assert palette.glow_soft == engine.parse_color(theme.extras["variant_tokens"][variant_id]["glow_soft"])


def test_stale_palette_drops_tones_and_halos():
    palette = Palette.for_variant(nostromo(), nostromo().variant("amber"))
    stale = palette.stale_tones()
    assert stale.main == stale.bright == stale.hot == palette.stale
    assert stale.glow[3] == 0 and stale.glow_soft[3] == 0
    assert stale.message == palette.message        # the auth box keeps its label tone


def test_variant_tokens_are_read_when_variant_has_no_extras():
    theme = nostromo()
    bare = replace(theme.variant("amber"), extras={})
    palette = Palette.for_variant(theme, bare)
    assert palette.ink == engine.parse_color(theme.extras["variant_tokens"]["amber"]["ink"])


# ── Floating widget: every variant x state x scale ───────────────────────────

@pytest.mark.parametrize("variant_id", ["amber", "green", "white", "red", "blue", "cyan", "orange", "purple"])
def test_every_variant_renders_every_state_at_every_scale(variant_id):
    theme = nostromo()
    variant = theme.variant(variant_id)
    skin = get_skin("nostromo")
    for name in ALL_STATES:
        model = dump.sample_model(name)
        for scale in SCALES:
            image = skin.render_floating(model, theme, variant, scale, None)
            height = FLOAT_HEIGHTS[name]
            assert image.mode == "RGBA"
            assert image.size == (round(280 * scale), round(height * scale)), (name, scale)
            low, high = alpha_extrema(image)
            assert low == 0 and high == 255, (name, scale)     # real alpha: clear corners, solid body


def test_floating_size_matches_rendered_size():
    skin = get_skin("nostromo")
    for name in ALL_STATES:
        model = dump.sample_model(name)
        for scale in SCALES:
            image = skin.render_floating(model, nostromo(), nostromo().variant("amber"), scale, None)
            assert skin.floating_size(nostromo(), model, scale) == image.size


def test_frame_height_is_94_for_two_rows_and_126_for_three():
    model_two = build_model(state(limit("session", 10), limit("weekly_all", 5)), NOW)
    model_three = build_model(state(limit("session", 10), limit("weekly_all", 5), scoped(3.0, active=True)), NOW)
    skin = get_skin("nostromo")
    assert skin.floating_size(nostromo(), model_two, 1.0) == (280, 94)
    assert skin.floating_size(nostromo(), model_three, 1.0) == (280, 126)


def test_transparent_background_removes_the_plate():
    theme = nostromo()
    variant = theme.variant("amber")
    skin = get_skin("nostromo")
    model = dump.sample_model("normal3")
    solid = skin.render_floating(model, theme, variant, 1.0, None)
    clear = skin.render_floating(model, theme, variant, 1.0, {"transparent_bg": True})
    solid_coverage = sum(1 for a in solid.getchannel("A").getdata() if a > 128)
    clear_coverage = sum(1 for a in clear.getchannel("A").getdata() if a > 128)
    assert clear_coverage < solid_coverage * 0.5
    assert clear.getpixel((0, 0))[3] == 0            # corners stay clear either way


def test_text_fx_off_removes_scanlines_from_the_plate():
    theme = nostromo()
    variant = theme.variant("amber")
    skin = get_skin("nostromo")
    model = dump.sample_model("stale")      # stale has no halos, so only the scanlines differ
    on = skin.render_floating(model, theme, variant, 1.0, {"text_fx": True})
    off = skin.render_floating(model, theme, variant, 1.0, {"text_fx": False})
    # The box x 150..230, y 62..74 holds no content. One row in three is a scanline row, which
    # darkens to 70 %, so the box averages about 0.9 of its unscanned brightness.
    box = (150, 62, 230, 74)
    mean_on = sum(on.convert("L").crop(box).getdata()) / ((box[2] - box[0]) * (box[3] - box[1]))
    mean_off = sum(off.convert("L").crop(box).getdata()) / ((box[2] - box[0]) * (box[3] - box[1]))
    assert mean_on < mean_off
    assert 0.85 <= mean_on / mean_off <= 0.95


def test_critical_state_draws_the_hazard_band_and_others_do_not():
    theme = nostromo()
    variant = theme.variant("amber")
    skin = get_skin("nostromo")

    def bright_in_band(image: Image.Image) -> int:
        count = 0
        for y in range(4):
            for x in range(image.width):
                r, g, b, a = image.getpixel((x, y))
                if a > 200 and r > 200 and g > 140 and b < 60:
                    count += 1
        return count

    assert bright_in_band(skin.render_floating(dump.sample_model("critical"), theme, variant, 1.0, None)) > 20
    assert bright_in_band(skin.render_floating(dump.sample_model("normal3"), theme, variant, 1.0, None)) == 0


def _rgb_at(image: Image.Image, x: float, y: float, scale: float) -> tuple[int, int, int]:
    """The colour at a logical coordinate of a frame rendered at ``scale`` (a cell interior is flat)."""
    return image.getpixel((round(x * scale), round(y * scale)))[:3]


def _near(actual: tuple[int, int, int], expected: tuple[int, ...]) -> bool:
    return all(abs(a - e) <= 2 for a, e in zip(actual, expected[:3]))


@pytest.mark.parametrize(("severity", "percent", "emphasis"), [
    (SEVERITY_NORMAL, 52.0, "main"),
    (SEVERITY_WARNING, 80.0, "bright"),
    (SEVERITY_CRITICAL, 90.0, "hot"),
])
def test_a_part_lit_pip_stays_main_while_the_full_pips_take_the_severity_colour(severity, percent, emphasis):
    """In the approved CSS only a full cell (.on) turns --bright for a warning and --hot for a critical
    limit; a part-lit cell (.part) is always --main. The skin used to light both in the severity colour,
    so the boundary cell looked one cell too bright."""
    theme = nostromo()
    palette = Palette.for_variant(theme, theme.variant("amber"))
    skin = get_skin("nostromo")
    model = build_model(state(limit("session", percent, severity=severity), limit("weekly_all", 10.0)), NOW)
    scale = 2.0
    image = skin.render_floating(model, theme, theme.variant("amber"), scale, {"text_fx": False})

    full, fraction = engine.split_segments(percent, 16)
    partial_w = round(fraction * 12)
    assert full < 16 and partial_w >= 4                      # the sample row really has a part-lit cell
    y = 28 + 21 + 4                                           # row 1, vertical middle of the 8 px pips
    full_cell = _rgb_at(image, 14 + 16 * (full - 1) + 6, y, scale)
    partial_cell = _rgb_at(image, 14 + 16 * full + partial_w / 2, y, scale)
    ghost_cell = _rgb_at(image, 14 + 16 * 15 + 6, y, scale)
    assert _near(full_cell, getattr(palette, emphasis)), (full_cell, getattr(palette, emphasis))
    assert _near(partial_cell, palette.main), (partial_cell, palette.main)
    assert _near(ghost_cell, palette.ghost)


@pytest.mark.parametrize(("severity", "emphasis"), [(SEVERITY_WARNING, "bright"), (SEVERITY_CRITICAL, "hot")])
def test_a_part_lit_strip_cell_stays_main_too(severity, emphasis):
    theme = nostromo()
    palette = Palette.for_variant(theme, theme.variant("amber"))
    model = build_model(state(limit("session", 95.0, severity=severity), limit("weekly_all", 10.0)), NOW)
    scale = 2.0
    image = get_skin("nostromo").render_taskbar(model, theme, theme.variant("amber"), 34, scale, {"text_fx": False})
    full, fraction = engine.split_segments(95.0, 10)         # 9 full cells and half of the tenth
    partial_w = round(fraction * 4)
    assert (full, partial_w) == (9, 2)
    y = 13 + 4
    assert _near(_rgb_at(image, 44 + 6 * 8 + 2, y, scale), getattr(palette, emphasis))
    assert _near(_rgb_at(image, 44 + 6 * 9 + 1, y, scale), palette.main)


def test_stale_and_live_render_differently():
    theme = nostromo()
    variant = theme.variant("amber")
    skin = get_skin("nostromo")
    live = skin.render_floating(dump.sample_model("normal2"), theme, variant, 1.0, None)
    stale = skin.render_floating(dump.sample_model("stale"), theme, variant, 1.0, None)
    assert live.tobytes() != stale.tobytes()


def test_reduce_motion_keeps_the_alive_mark_lit():
    theme = nostromo()
    variant = theme.variant("amber")
    skin = get_skin("nostromo")
    dim = build_model(dump.sample_state("normal2"), NOW, blink_on=False)
    lit_still = skin.render_floating(dim, theme, variant, 1.0, {"reduce_motion": True})
    blink_off = skin.render_floating(dim, theme, variant, 1.0, {"reduce_motion": False})
    assert lit_still.tobytes() != blink_off.tobytes()


def test_rendering_is_deterministic():
    theme = nostromo()
    variant = theme.variant("red")
    model = dump.sample_model("warning")
    first = NostromoSkin().render_floating(model, theme, variant, 1.25, None)
    second = NostromoSkin().render_floating(model, theme, variant, 1.25, None)
    assert first.tobytes() == second.tobytes()


def test_floating_render_is_fast_enough():
    theme = nostromo()
    variant = theme.variant("amber")
    skin = get_skin("nostromo")
    model = dump.sample_model("normal3")
    skin.render_floating(model, theme, variant, 1.0, None)        # warm the font cache
    started = time.perf_counter()
    for _ in range(5):
        skin.render_floating(model, theme, variant, 1.0, None)
    per_frame_ms = (time.perf_counter() - started) / 5 * 1000
    assert per_frame_ms < 150, f"floating frame took {per_frame_ms:.1f} ms"


# ── Taskbar strip ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("height", "scale", "size"), [
    (48, 1.0, (264, 48)),
    (40, 1.0, (264, 40)),
    (72, 1.5, (396, 72)),
    (96, 2.0, (528, 96)),
])
def test_taskbar_strip_size_and_placement(height, scale, size):
    theme = nostromo()
    image = get_skin("nostromo").render_taskbar(dump.sample_model("normal3"), theme, theme.variant("amber"),
                                                height, scale, None)
    assert image.size == size
    assert alpha_extrema(image) == (0, 255)
    assert image.getchannel("A").crop((0, 0, size[0], 1)).getextrema() == (0, 0)   # strip is centred


@pytest.mark.parametrize("name", ALL_STATES)
def test_taskbar_strip_renders_every_state(name):
    theme = nostromo()
    image = get_skin("nostromo").render_taskbar(dump.sample_model(name), theme, theme.variant("cyan"), 34, 1.0,
                                                None)
    assert image.size == (264, 34)
    assert alpha_extrema(image)[1] == 255


# ── Tooltip ───────────────────────────────────────────────────────────────────

def test_tooltip_sizes_follow_row_count_and_message():
    theme = nostromo()
    skin = get_skin("nostromo")
    variant = theme.variant("amber")
    assert skin.render_tooltip(dump.sample_model("normal3"), theme, variant, 1.0).size == (280, 184)
    assert skin.render_tooltip(dump.sample_model("normal2"), theme, variant, 1.0).size == (280, 134)
    assert skin.render_tooltip(dump.sample_model("stale"), theme, variant, 1.0).size == (280, 170)
    assert skin.render_tooltip(dump.sample_model("loading"), theme, variant, 2.0).size == (560, 268)


def test_tooltip_renders_every_state_and_variant():
    theme = nostromo()
    skin = get_skin("nostromo")
    for variant in theme.variants:
        for name in ALL_STATES:
            image = skin.render_tooltip(dump.sample_model(name), theme, variant, 1.0)
            assert image.mode == "RGBA" and alpha_extrema(image) == (0, 255)


# ── Tray icon ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("size", [16, 20, 24, 32])
def test_tray_icon_size_and_plate(size):
    theme = nostromo()
    image = get_skin("nostromo").render_tray_icon(dump.sample_model("normal3"), theme, theme.variant("amber"), size)
    assert image.size == (size, size)
    assert image.getpixel((0, 0))[3] == 0          # chamfered corner is clear
    assert image.getpixel((size // 2, 1))[3] == 255  # plate edge is opaque


@pytest.mark.parametrize("size", [16, 20, 24])
def test_small_tray_icons_have_no_anti_aliasing(size):
    theme = nostromo()
    for name in ("normal3", "warning", "critical", "stale"):
        image = get_skin("nostromo").render_tray_icon(dump.sample_model(name), theme, theme.variant("amber"), size)
        colours = {pixel for pixel in image.getdata()}
        assert len(colours) <= 6, (name, size, len(colours))


def test_tray_icon_centre_marks_match_the_approved_geometry():
    theme = nostromo()
    skin = get_skin("nostromo")
    palette = Palette.for_variant(theme, theme.variant("amber"))
    image = skin.render_tray_icon(dump.sample_model("normal3"), theme, theme.variant("amber"), 16)
    for x in (7, 8):
        for y in (7, 8):
            assert image.getpixel((x, y)) == palette.main
    assert image.getpixel((6, 6)) != palette.main or image.getpixel((9, 9)) != palette.main  # arc, not solid


def test_tray_stale_icon_has_no_centre_and_no_solid_arc():
    theme = nostromo()
    palette = Palette.for_variant(theme, theme.variant("amber"))
    image = get_skin("nostromo").render_tray_icon(dump.sample_model("stale"), theme, theme.variant("amber"), 24)
    assert palette.main not in set(image.getdata())
    assert palette.stale in set(image.getdata())


def test_tray_colour_follows_severity():
    theme = nostromo()
    palette = Palette.for_variant(theme, theme.variant("amber"))
    skin = get_skin("nostromo")
    warning = skin.render_tray_icon(dump.sample_model("warning"), theme, theme.variant("amber"), 24)
    critical = skin.render_tray_icon(dump.sample_model("critical"), theme, theme.variant("amber"), 24)
    assert palette.bright in set(warning.getdata())
    assert palette.hot in set(critical.getdata())


def test_tray_icon_rejects_tiny_sizes():
    theme = nostromo()
    with pytest.raises(ValueError):
        get_skin("nostromo").render_tray_icon(dump.sample_model("normal3"), theme, theme.variant("amber"), 4)


def test_tray_icon_is_deterministic():
    theme = nostromo()
    skin = NostromoSkin()
    first = skin.render_tray_icon(dump.sample_model("warning"), theme, theme.variant("amber"), 32)
    second = skin.render_tray_icon(dump.sample_model("warning"), theme, theme.variant("amber"), 32)
    assert first.tobytes() == second.tobytes()


# ── Dump helper ───────────────────────────────────────────────────────────────

def test_shipped_nostromo_theme_has_eight_variants_and_the_expected_fonts():
    theme = nostromo()
    assert theme.name == "Nostromo"
    assert len(theme.variants) == 8
    assert theme.variant(None).id == "amber"
    assert theme.variant("amber").colors["bg"] == "#090909"
    assert theme.fonts["body"]["family"] == "IBM Plex Mono"
    assert theme.fonts["mono"]["family"] == "VT323"


def test_theme_objects_include_the_shipped_themes():
    assert {"nostromo", "night_city", "gates"} <= set(themes())


def test_dump_frames_writes_the_expected_files(tmp_path: Path):
    report = dump.dump_frames(tmp_path, themes=["nostromo"], variants=["amber"], states=["normal3", "critical"],
                              scales=[1.0, 2.0])
    variant_dir = tmp_path / "nostromo" / "amber"
    assert report.files == 2 * 2 * (1 + 2 + 1) + 2 * 4      # floating + 2 strips + tooltip per state/scale, tray x4
    assert (variant_dir / "floating_normal3_x1.png").is_file()
    assert (variant_dir / "floating_critical_x2.png").is_file()
    assert (variant_dir / "taskbar_normal3_h48_x1.png").is_file()
    assert (variant_dir / "taskbar_normal3_h96_x2.png").is_file()
    assert (variant_dir / "tooltip_critical_x1.png").is_file()
    assert (variant_dir / "tray_critical_32.png").is_file()
    sheet = report.sheets[0]
    assert sheet.is_file() and sheet.name == "contact_sheet.png"
    with Image.open(sheet) as opened:
        assert opened.format == "PNG" and opened.width > 300


def test_dump_frames_rejects_unknown_states(tmp_path: Path):
    with pytest.raises(KeyError):
        dump.dump_frames(tmp_path, themes=["nostromo"], states=["sideways"])


def test_unknown_theme_is_reported(tmp_path: Path):
    with pytest.raises(KeyError):
        dump.dump_frames(tmp_path, themes=["no-such-theme"])
