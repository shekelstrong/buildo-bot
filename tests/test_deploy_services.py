"""Tests for the scenes SVG generator.

Validates that all scenes render to valid PNG bytes of expected size.
"""

from bot.services.scenes import SCENES, get_scene


def test_all_scenes_registered():
    expected = {
        "welcome",
        "no_sites",
        "generating",
        "editing",
        "published",
        "menu",
        "error",
        "referral",
        "payment",
        "admin",
    }
    assert set(SCENES.keys()) == expected


def test_get_scene_returns_png():
    data = get_scene("welcome")
    assert isinstance(data, bytes)
    assert len(data) > 1000  # not empty
    # PNG magic bytes
    assert data[:8] == b"\x89PNG\r\n\x1a\n"


def test_all_scenes_render_to_png():
    """All scenes should render without error."""
    for name in SCENES:
        data = get_scene(name)
        assert isinstance(data, bytes)
        assert len(data) > 1000, f"{name} too small: {len(data)} bytes"


def test_unknown_scene_falls_back_to_welcome():
    data = get_scene("does_not_exist")
    welcome = get_scene("welcome")
    assert data == welcome
