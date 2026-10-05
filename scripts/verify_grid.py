"""Executable pixel-equality proof: offline overlay grid vs live GEE projection.

For one (dataset, lon, lat) point:
  OVERLAY  = cell corners computed offline by
             src.infrastructure.utils.grid_utils.generate_pixel_grid_geojson,
             driven by the crs/transform SHIPPED in config/satellites.json
  EXPECTED = cell corners from the LIVE ee.Image(<first-image>).projection(),
             applying the same floor math to the point
PASS iff max abs corner error <= 1e-6 degrees. Exit 0 iff PASS.

Usage:
  python scripts/verify_grid.py --dataset CHIRPS_DAILY --lon 12.35 --lat 45.05
"""
import argparse
import json
import math
import sys
from pathlib import Path

import ee
from shapely.geometry import box

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.infrastructure.utils.grid_utils import generate_pixel_grid_geojson

# probe_projection (same scripts/ dir, auto on sys.path when run as __main__)
# owns the shared GEE helpers — single load_project, single load_gated.
from probe_projection import CATALOG, first_image, live_projection, load_gated, load_project

EPS = 1e-6
MARGIN = 1e-4  # deg; ROI half-width, keeps the ROI strictly inside one cell


def load_entry(dataset):
    for sat in json.loads(CATALOG.read_text(encoding="utf-8"))["satellites"]:
        if sat["id"] == dataset:
            return sat
    raise SystemExit(f"dataset '{dataset}' not found in config/satellites.json")


def cell_corners(lon, lat, transform):
    """Corners of the cell containing the point — same floor math as grid_utils:
    col = floor((x - c) / a), row = floor((y - f) / e)."""
    a, _b, c, _d, e, f = transform
    col = math.floor((lon - c) / a)
    row = math.floor((lat - f) / e)
    x0, x1 = sorted((col * a + c, (col + 1) * a + c))
    y0, y1 = sorted((row * e + f, (row + 1) * e + f))
    return [(x0, y0), (x0, y1), (x1, y1), (x1, y0)]


def overlay_corners(lon, lat, crs, transform):
    """Cell corners as the shipped overlay renders them: clamp the point into
    its config-derived cell, build a tiny ROI strictly inside, run grid_utils."""
    a, _b, c, _d, e, f = transform
    xs = [p[0] for p in cell_corners(lon, lat, transform)]
    ys = [p[1] for p in cell_corners(lon, lat, transform)]
    cx = min(max(lon, min(xs) + 2 * MARGIN), max(xs) - 2 * MARGIN)
    cy = min(max(lat, min(ys) + 2 * MARGIN), max(ys) - 2 * MARGIN)
    roi = box(cx - MARGIN, cy - MARGIN, cx + MARGIN, cy + MARGIN)
    grid = generate_pixel_grid_geojson(roi, crs, transform, max_pixels=100)
    if not grid or grid.get("properties", {}).get("error"):
        raise SystemExit(f"overlay grid failed: {grid}")
    feats = grid["features"]
    if len(feats) != 1:
        raise SystemExit(f"expected exactly 1 overlay cell, got {len(feats)}")
    return feats[0]["geometry"]["coordinates"][0][:4]


def main():
    ap = argparse.ArgumentParser(description="Verify shipped pixel-grid metadata matches live GEE.")
    ap.add_argument("--dataset", required=True, choices=list(load_gated()))
    ap.add_argument("--project", default=None,
                    help="GEE project id (default: config/settings.toml [gee] project_id)")
    ap.add_argument("--lon", type=float, required=True)
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--tamper-shift-c", type=float, default=0.0,
                    help="sensitivity check: shift the LIVE transform c by this much "
                         "before diffing (simulates a half-pixel config error)")
    args = ap.parse_args()

    ee.Initialize(project=load_project(args.project))
    sat = load_entry(args.dataset)
    cfg_crs, cfg_tf = sat["crs"], sat["transform"]

    image_id, date = first_image(sat["ee_collection_name"])
    proj, source = live_projection(image_id)
    live_tf = list(proj["transform"])
    live_tf[2] += args.tamper_shift_c

    print(f"dataset={args.dataset} point=({args.lon}, {args.lat})")
    print(f"image={image_id} date={date} projection_source={source}")
    print(f"config crs={cfg_crs} transform={cfg_tf}")
    print(f"live   crs={proj['crs']} transform={live_tf}")

    overlay = overlay_corners(args.lon, args.lat, cfg_crs, cfg_tf)
    expected = cell_corners(args.lon, args.lat, live_tf)
    max_err = 0.0
    for i, (ov, ex) in enumerate(zip(overlay, expected)):
        err = max(abs(ov[0] - ex[0]), abs(ov[1] - ex[1]))
        max_err = max(max_err, err)
        print(f"corner {i}: overlay=({ov[0]:.6f}, {ov[1]:.6f}) expected=({ex[0]:.6f}, {ex[1]:.6f}) err={err:.3g}")

    ok = max_err <= EPS and proj["crs"] == cfg_crs
    print(f"max_err={max_err:.3g} eps={EPS} -> {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
