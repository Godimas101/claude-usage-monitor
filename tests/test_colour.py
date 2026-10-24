"""Tests for usage_monitor.themes.colour: contrast, CIE76, OKLCH and the system-accent lift.

Pure functions, no I/O. The reference values are published ones (WCAG's #767676 on white, sRGB red in
CIE Lab and OKLCH), so a slip in a matrix cannot hide behind the code that uses the function.
"""
from __future__ import annotations

import random

import pytest

from usage_monitor.themes import colour


def test_contrast_ratio_matches_the_wcag_reference_values() -> None:
    assert colour.contrast_ratio("#ffffff", "#000000") == pytest.approx(21.0)
    assert colour.contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0)
    assert colour.contrast_ratio("#777777", "#777777") == pytest.approx(1.0)
    assert colour.contrast_ratio("#767676", "#ffffff") == pytest.approx(4.54, abs=0.005)   # the lightest grey that passes AA


def test_delta_e76_matches_known_lab_distances() -> None:
    assert colour.delta_e76("#ffffff", "#000000") == pytest.approx(100.0, abs=0.01)
    assert colour.delta_e76("#336699", "#336699") == 0.0
    # sRGB red is Lab (53.24, 80.09, 67.20); white is (100, 0, 0)
    assert colour.delta_e76("#ff0000", "#ffffff") == pytest.approx(114.5, abs=0.1)


def test_oklch_matches_the_reference_values() -> None:
    lightness, chroma, hue = colour.to_oklch("#ff0000")
    assert (round(lightness, 4), round(chroma, 4), round(hue, 2)) == (0.6280, 0.2577, 29.23)
    assert colour.to_oklch("#ffffff")[0] == pytest.approx(1.0, abs=1e-4)
    assert colour.to_oklch("#000000")[0] == pytest.approx(0.0, abs=1e-6)


def test_oklch_round_trips_every_colour_it_is_given() -> None:
    rng = random.Random(7)
    for _ in range(400):
        value = "#" + "".join(f"{rng.randrange(256):02x}" for _ in range(3))
        assert colour.from_oklch(*colour.to_oklch(value)) == value


def test_an_out_of_gamut_value_loses_chroma_but_keeps_lightness_and_hue() -> None:
    mapped = colour.from_oklch(0.7, 0.4, 145)            # far more chroma than sRGB green can carry
    lightness, chroma, hue = colour.to_oklch(mapped)
    assert lightness == pytest.approx(0.7, abs=0.01)
    assert hue == pytest.approx(145, abs=3)
    assert 0.1 < chroma < 0.4


def test_hex_helpers_ignore_alpha_and_round_and_clamp() -> None:
    assert colour.parse_hex("#12345678") == (0x12, 0x34, 0x56)
    assert colour.to_hex((0.4, 254.6, 300.0)) == "#00ffff"


@pytest.mark.parametrize(("accent", "mode", "expected"), [
    ("#d0000c", "dark", "#ff5145"),        # the owner's Windows accent, as the browser drew it
    ("#d0000c", "light", "#d0000c"),       # already dark enough for a light window
    ("#123456", "dark", "#7a9fc7"),
    ("#abcdef", "light", "#6484a3"),
])
def test_the_lift_moves_lightness_into_the_window_bound_and_nothing_else(accent: str, mode: str, expected: str) -> None:
    lifted = colour.lift_accent(accent, mode)
    assert lifted == expected
    lightness, chroma, hue = colour.to_oklch(lifted)
    if mode == "dark":
        assert lightness >= colour.LIGHTNESS_MIN_DARK - 0.02       # gamut mapping may clip within one JND (0.02)
    else:
        assert lightness <= colour.LIGHTNESS_MAX_LIGHT + 0.02
    if accent != lifted:
        assert hue == pytest.approx(colour.to_oklch(accent)[2], abs=4)


@pytest.mark.parametrize("accent", ["#5b9dff", "#4dd6c2", "#b39ae6", "#ff8ac5", "#ff9f5a", "#c3e86b"])
def test_a_colour_inside_the_dark_bound_is_returned_unchanged(accent: str) -> None:
    assert colour.lift_accent(accent.upper(), "dark") == accent


@pytest.mark.parametrize("accent", ["#2d5bd9", "#018574", "#6f4fa8", "#c10e74", "#c75300", "#5a7f00"])
def test_a_colour_inside_the_light_bound_is_returned_unchanged(accent: str) -> None:
    assert colour.lift_accent(accent, "light") == accent


def test_the_lift_keeps_going_until_the_surfaces_are_reached() -> None:
    """The bounds already give 3:1 on the real cards (see the sweep in test_themes). On a surface they do not
    suit, the lightness keeps moving the same way: up on a dark window, down on a light one."""
    for mode, accent, surface in (("dark", "#123456", "#6a6a6a"), ("light", "#c8e600", "#9a9a9a")):
        bound_only = colour.lift_accent(accent, mode)
        assert colour.contrast_ratio(bound_only, surface) < 3.0, mode
        reached = colour.lift_accent(accent, mode, [surface, "#202020" if mode == "dark" else "#ffffff"])
        assert colour.contrast_ratio(reached, surface) >= 3.0, mode
        moved = colour.to_oklch(reached)[0] - colour.to_oklch(bound_only)[0]
        assert moved > 0 if mode == "dark" else moved < 0


def test_the_lift_stops_at_the_end_of_the_lightness_range() -> None:
    # nothing is 3:1 against a mid grey in both directions; it must stop, not loop or fail
    assert colour.lift_accent("#123456", "dark", ["#bbbbbb"]).startswith("#")
    assert colour.lift_accent("#123456", "light", ["#444444"]).startswith("#")


def test_min_separation_is_the_nearest_neighbour_and_infinite_for_none() -> None:
    assert colour.min_separation("#ff0000", ["#ffffff", "#ff0001", "#00ff00"]) < 1
    assert colour.min_separation("#ff0000", []) == float("inf")
    assert colour.min_separation("#ff0000", ["#ffffff80"]) == pytest.approx(114.5, abs=0.1)
