"""Preset/history backward compatibility with the pixel-grid feature (todo 9).

Old share payloads (main@0f9ae52 era) never carried crs/transform: presets
store ``config["satellite"]`` as an ID string that is re-resolved from
``config/satellites.json`` at load. These tests prove that such payloads
still load cleanly and that the grid toggle predicate degrades to False
(toggle hidden entirely) for datasets without grid metadata — with no
exception anywhere.
"""
import copy

import pytest

from src.infrastructure.persistence.PresetSerializer import (
    PresetValidationError,
    from_share_payload,
)
from src.interface.main_panel import (
    active_satellite_supports_grid,
    load_satellites,
)

OLD_SHARE_PAYLOAD = {
    "schema_version": 1,
    "type": "gee_extraction_preset",
    "name": "Old",
    "config": {
        "satellite": "CHIRPS_DAILY",
        "bands": ["precipitation"],
        "geometry_source": "GADM",
        "dates": {"start_year": 2000, "end_year": 2001},
        "export_method": "Drive",
    },
}


def resolve_satellite(sat_id):
    """Resolve a preset's satellite ID via the real catalog; None if unknown."""
    return next((s for s in load_satellites() if s["id"] == sat_id), None)


def test_old_chirps_preset_loads_and_grid_available():
    draft = from_share_payload(copy.deepcopy(OLD_SHARE_PAYLOAD))

    assert draft["config"]["satellite"] == "CHIRPS_DAILY"
    sat = resolve_satellite(draft["config"]["satellite"])
    assert sat is not None
    assert active_satellite_supports_grid(sat) is True


def test_unsupported_datasets_load_but_grid_hidden():
    # MODIS lacks crs/transform in the catalog: the preset still
    # loads, but the toggle predicate is False so the checkbox never renders.
    # (SMAP originally shared this test — todo 13 gave it verified grid
    # metadata, so it now asserts True in its own test below.)
    for sat_id in ("MODIS_MOD13Q1_061",):
        payload = copy.deepcopy(OLD_SHARE_PAYLOAD)
        payload["config"]["satellite"] = sat_id

        draft = from_share_payload(payload)

        assert draft["config"]["satellite"] == sat_id
        assert active_satellite_supports_grid(resolve_satellite(sat_id)) is False


def test_old_smap_preset_loads_and_grid_available():
    # Old-era SMAP payload (pre-todo-13: no crs/transform in the catalog)
    # still loads cleanly, and the verified SMAP grid metadata added in
    # todo 13 (EPSG:4326, ~0.095 deg cells, pixelSize 11000 >= 1000) makes
    # the toggle predicate True.
    payload = copy.deepcopy(OLD_SHARE_PAYLOAD)
    payload["config"]["satellite"] = "NASA_SMAP_SPL4SMGP_008"

    draft = from_share_payload(payload)

    assert draft["config"]["satellite"] == "NASA_SMAP_SPL4SMGP_008"
    sat = resolve_satellite(draft["config"]["satellite"])
    assert sat is not None
    assert sat["crs"] == "EPSG:4326"
    assert len(sat["transform"]) == 6
    assert active_satellite_supports_grid(sat) is True


def test_worldcereal_is_a_mask_not_a_satellite():
    # ESA_WORLDCEREAL_V100 lives under `masks`, not `satellites`, so it can
    # never be selected as a dataset and can never activate the toggle.
    assert resolve_satellite("ESA_WORLDCEREAL_V100") is None
    assert active_satellite_supports_grid(None) is False


def test_unknown_satellite_no_exception_grid_hidden():
    payload = copy.deepcopy(OLD_SHARE_PAYLOAD)
    payload["config"]["satellite"] = "DOES_NOT_EXIST"

    draft = from_share_payload(payload)  # must not raise

    assert draft["config"]["satellite"] == "DOES_NOT_EXIST"
    assert resolve_satellite("DOES_NOT_EXIST") is None
    assert active_satellite_supports_grid(None) is False


def test_satellite_snapshot_without_grid_metadata_pred_false():
    # A full satellite snapshot (as history entries sometimes embedded) that
    # predates crs/transform: predicate False both on the raw snapshot and
    # after round-tripping through a share payload.
    sat = resolve_satellite("MODIS_MOD13Q1_061")
    snapshot = {k: v for k, v in sat.items() if k not in ("crs", "transform")}

    assert active_satellite_supports_grid(snapshot) is False

    payload = copy.deepcopy(OLD_SHARE_PAYLOAD)
    payload["config"]["satellite"] = snapshot
    draft = from_share_payload(payload)  # must not raise

    assert resolve_satellite(draft["config"]["satellite"]) is None
    assert active_satellite_supports_grid(draft["config"]["satellite"]) is False


def test_history_style_entry_converted_with_warning():
    history_entry = {
        "satellite": "CHIRPS_DAILY",
        "bands": ["precipitation"],
        "dates": {"start_year": 2000, "end_year": 2001},
    }

    draft = from_share_payload(copy.deepcopy(history_entry))

    assert any("Converted a history-style entry" in w for w in draft["warnings"])
    assert draft["config"]["satellite"] == "CHIRPS_DAILY"
    assert active_satellite_supports_grid(resolve_satellite("CHIRPS_DAILY")) is True


def test_garbage_still_rejected():
    with pytest.raises(PresetValidationError):
        from_share_payload({"unrelated": True})
