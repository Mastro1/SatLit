"""View persistence (todo 14): roi hash stability + plugin HTML payload."""
from src.interface.map_utils import (
    ViewPersistencePlugin,
    _finalize,
    create_base_map,
    roi_view_hash,
)


def test_hash_stable_identical():
    b = [12.3, 45.0, 12.4, 45.1]
    assert roi_view_hash(b) == roi_view_hash(list(b))


def test_hash_changes_on_bound_shift():
    b = [12.3, 45.0, 12.4, 45.1]
    other = [12.3, 45.0, 12.5, 45.1]
    assert roi_view_hash(b) != roi_view_hash(other)


def test_hash_rounding_insensitive_to_float_noise():
    b = [12.3, 45.0, 12.4, 45.1]
    noisy = [x + 1e-9 for x in b]
    assert roi_view_hash(b) == roi_view_hash(noisy)


def test_finalize_embeds_plugin_payload():
    bounds = [12.3, 45.0, 12.4, 45.1]
    expected = roi_view_hash(bounds)
    m = create_base_map(center=[45.05, 12.35], zoom=5)
    _finalize(m, "test_view_map", bounds)
    html = m.get_root().render()
    assert "folium_view_" in html
    assert expected in html
    assert "setView" in html
    assert "baselayerchange" in html


def test_finalize_without_bounds_attaches_nothing():
    m = create_base_map(center=[0, 0], zoom=5)
    _finalize(m, "test_view_map_none", None)
    html = m.get_root().render()
    assert "folium_view_test_view_map_none" not in html
    assert not [
        c for c in m._children.values() if isinstance(c, ViewPersistencePlugin)
    ]
