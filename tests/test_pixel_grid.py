"""Pixel-math unit tests for generate_pixel_grid_geojson (viz-only grid module).

Expected values are hand-computed worked examples (see
.omo/notepads/pixel-grid-viz/learnings.md), NOT re-derived from the
implementation formula. ROIs are strictly inside a single grid cell so results
are deterministic.
"""

import time

import pytest
from shapely.geometry import Polygon

from src.infrastructure.utils.grid_utils import generate_pixel_grid_geojson

CHIRPS_TRANSFORM = [0.05, 0, -180, 0, -0.05, 50]
ERA5_TRANSFORM = [0.1, 0, -180.05, 0, -0.1, 90.05]
UTM33N_TRANSFORM = [10, 0, 500000, 0, -10, 5000000]
TOL = 1e-9


def _box(min_lon, min_lat, max_lon, max_lat):
    return Polygon([
        (min_lon, min_lat),
        (max_lon, min_lat),
        (max_lon, max_lat),
        (min_lon, max_lat),
    ])


def _extent(feature):
    ring = feature["geometry"]["coordinates"][0]
    xs = [pt[0] for pt in ring]
    ys = [pt[1] for pt in ring]
    return min(xs), max(xs), min(ys), max(ys)


def test_chirps_worked_example_single_cell():
    # Given: CHIRPS 0.05-deg grid, ROI strictly inside cell col=3847, row=99
    roi = _box(12.36, 45.01, 12.39, 45.04)
    # When: the pixel grid is generated
    fc = generate_pixel_grid_geojson(roi, "EPSG:4326", CHIRPS_TRANSFORM)
    # Then: exactly one feature — the hand-verified cell
    assert fc is not None
    assert fc["properties"]["total_pixels"] == 1
    (feature,) = fc["features"]
    assert feature["properties"]["col"] == 3847
    assert feature["properties"]["row"] == 99
    assert feature["properties"]["col_row"] == "3847_99"
    x_min, x_max, y_min, y_max = _extent(feature)
    assert x_min == pytest.approx(12.35, abs=TOL)
    assert x_max == pytest.approx(12.40, abs=TOL)
    assert y_min == pytest.approx(45.00, abs=TOL)
    assert y_max == pytest.approx(45.05, abs=TOL)
    assert feature["properties"]["center_lon"] == pytest.approx(12.375, abs=TOL)
    assert feature["properties"]["center_lat"] == pytest.approx(45.025, abs=TOL)


def test_era5_land_worked_example_single_cell():
    # Given: ERA5-Land 0.1-deg grid with half-pixel offset,
    # ROI strictly inside cell col=1924, row=450
    roi = _box(12.37, 44.97, 12.43, 45.03)
    # When: the pixel grid is generated
    fc = generate_pixel_grid_geojson(roi, "EPSG:4326", ERA5_TRANSFORM)
    # Then: exactly one feature — the hand-verified cell
    assert fc is not None
    assert fc["properties"]["total_pixels"] == 1
    (feature,) = fc["features"]
    assert feature["properties"]["col"] == 1924
    assert feature["properties"]["row"] == 450
    assert feature["properties"]["col_row"] == "1924_450"
    x_min, x_max, y_min, y_max = _extent(feature)
    assert x_min == pytest.approx(12.35, abs=TOL)
    assert x_max == pytest.approx(12.45, abs=TOL)
    assert y_min == pytest.approx(44.95, abs=TOL)
    assert y_max == pytest.approx(45.05, abs=TOL)
    assert feature["properties"]["center_lon"] == pytest.approx(12.40, abs=TOL)
    assert feature["properties"]["center_lat"] == pytest.approx(45.00, abs=TOL)


def test_floor_at_cell_corner_gives_lower_cell():
    # Given: the ERA5-Land point (12.35, 45.05) sits exactly on cell (1924, 450)'s
    # top-left corner; a tiny box tucked just inside that corner must land there
    roi = _box(12.3501, 45.0401, 12.3509, 45.0499)
    # When: the pixel grid is generated
    fc = generate_pixel_grid_geojson(roi, "EPSG:4326", ERA5_TRANSFORM)
    # Then: the corner point maps to cell (1924, 450)
    (feature,) = fc["features"]
    assert feature["properties"]["col"] == 1924
    assert feature["properties"]["row"] == 450


def test_floor_takes_lower_index_below_boundary():
    # Given: a tiny box strictly below the col 1924|1925 boundary at lon 12.45
    # (lon 12.4499 -> col 1924.999; floor gives 1924, a round-based impl gives 1925)
    roi = _box(12.4495, 44.99, 12.4499, 45.04)
    # When: the pixel grid is generated
    fc = generate_pixel_grid_geojson(roi, "EPSG:4326", ERA5_TRANSFORM)
    # Then: the LOWER index wins
    assert fc["properties"]["total_pixels"] == 1
    (feature,) = fc["features"]
    assert feature["properties"]["col"] == 1924
    assert feature["properties"]["col"] != 1925


def test_antimeridian_east_and_west_are_distinct():
    # Given: tiny boxes hugging +180 and -180 on the ERA5-Land grid
    east = generate_pixel_grid_geojson(
        _box(179.96, 44.99, 179.99, 45.01), "EPSG:4326", ERA5_TRANSFORM)
    west = generate_pixel_grid_geojson(
        _box(-179.94, 44.99, -179.91, 45.01), "EPSG:4326", ERA5_TRANSFORM)
    # Then: col 3600 vs col 1 — distinct cells, no wrap-merge
    (feast,) = east["features"]
    (fwest,) = west["features"]
    assert feast["properties"]["col"] == 3600
    assert fwest["properties"]["col"] == 1
    assert feast["properties"]["col_row"] != fwest["properties"]["col_row"]


def test_too_many_pixels_guard():
    # Given: a near-global ROI against max_pixels=1000
    roi = Polygon([(-179, -89), (179, -89), (179, 89), (-179, 89)])
    # When: the pixel grid is generated
    fc = generate_pixel_grid_geojson(roi, "EPSG:4326", ERA5_TRANSFORM)
    # Then: empty features + error payload, no exception
    assert fc["features"] == []
    assert fc["properties"]["error"] == "too_many_pixels"


def test_rotated_transform_raises():
    # Given: a transform with a non-zero rotation parameter (b=0.5)
    roi = _box(12.36, 45.01, 12.39, 45.04)
    # When/Then: generation raises ValueError
    with pytest.raises(ValueError, match="Rotated transforms"):
        generate_pixel_grid_geojson(
            roi, "EPSG:4326", [0.1, 0.5, -180.05, 0, -0.1, 90.05])


def test_invalid_inputs_return_none():
    # Given: degenerate crs/transform inputs
    roi = _box(12.36, 45.01, 12.39, 45.04)
    # Then: each guard returns None
    assert generate_pixel_grid_geojson(
        roi, "EPSG:4326", [0.1, 0, -180.05, 0, -0.1]) is None  # len < 6
    assert generate_pixel_grid_geojson(roi, None, ERA5_TRANSFORM) is None
    assert generate_pixel_grid_geojson(roi, "EPSG:4326", None) is None


def test_non_4326_reprojection_path():
    # Given: a synthetic UTM 33N grid (10 m pixels, origin 500000/5000000)
    # and a tiny lon/lat box around (15.0, 45.0) — zone 33N central meridian
    roi = _box(14.999, 44.999, 15.001, 45.001)
    # When: the pixel grid is generated
    fc = generate_pixel_grid_geojson(roi, "EPSG:32633", UTM33N_TRANSFORM)
    # Then: pixels come back and geometry is converted to lon/lat
    assert fc is not None
    assert fc["properties"]["total_pixels"] > 0
    pts = [pt
           for f in fc["features"]
           for ring in f["geometry"]["coordinates"]
           for pt in ring]
    xs = [pt[0] for pt in pts]
    ys = [pt[1] for pt in pts]
    assert -180 <= min(xs) < max(xs) <= 180
    assert -90 <= min(ys) < max(ys) <= 90


def test_guard_1000_cells_passes():
    # Given: a ROI whose edges sit on ERA5-Land cell MIDPOINTS, spanning exactly
    # 40 cols x 25 rows = 1000 cells (x in [c+0.5a, c+39.5a], y in [f-24.5|e|, f-0.5|e|])
    roi = _box(-180.00, 87.60, -176.10, 90.00)
    # When: the pixel grid is generated with the default max_pixels=1000
    fc = generate_pixel_grid_geojson(roi, "EPSG:4326", ERA5_TRANSFORM)
    # Then: a REAL grid — the guard is a strict > comparison, 1000 is allowed
    assert fc is not None
    assert "error" not in fc["properties"]
    assert fc["properties"]["total_pixels"] == 1000
    assert len(fc["features"]) == 1000


def test_guard_1001_cells_rejected():
    # Given: a ROI whose edges sit on ERA5-Land cell MIDPOINTS, spanning exactly
    # 143 cols x 7 rows = 1001 cells (x in [c+0.5a, c+142.5a], y in [f-6.5|e|, f-0.5|e|])
    roi = _box(-180.00, 89.40, -165.80, 90.00)
    # When: the pixel grid is generated with the default max_pixels=1000
    fc = generate_pixel_grid_geojson(roi, "EPSG:4326", ERA5_TRANSFORM)
    # Then: the too_many_pixels payload with empty features — one cell over fails
    assert fc["features"] == []
    assert fc["properties"]["error"] == "too_many_pixels"
    assert fc["properties"]["message"] == (
        "Grid contains 1001 pixels (limit: 1000). "
        "Please zoom in or use a smaller region."
    )


def test_timing_single_cell_under_3s():
    # Given: the CHIRPS single-cell reference AOI (same box as the worked example)
    roi = _box(12.36, 45.01, 12.39, 45.04)
    # When: generation is timed with perf_counter
    t0 = time.perf_counter()
    fc = generate_pixel_grid_geojson(roi, "EPSG:4326", CHIRPS_TRANSFORM)
    elapsed = time.perf_counter() - t0
    # Then: correct result, wall time far under the generous 3s bound
    assert fc is not None
    assert fc["properties"]["total_pixels"] == 1
    assert elapsed < 3.0
    print(f"\ngenerate_pixel_grid_geojson wall time (CHIRPS single cell): {elapsed * 1000:.2f} ms")
