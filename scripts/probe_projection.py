"""Live GEE projection proof for pixel-grid-viz todo 2.

Probes one image per gated dataset family and diffs the returned
projection against the plan's truth table. Exit 0 iff all MATCH.

Float comparison: a transform value matches iff abs(live - expected) <= 1e-9.
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ee

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / ".omo/evidence/task-2-pixel-grid-viz.json"
PROJECT = "asrdownscalingcropdata"
EPS = 1e-9

# Truth table from the pixel-grid-viz plan. Collection IDs read from
# config/satellites.json. All 5 gated SHOW datasets are probed, plus SMAP
# (todo 13): ERA5_LAND_HOURLY shares ERA5_LAND_DAILY_AGGR's grid; ERA5_HOURLY
# is the coarse 0.25 deg ECMWF/ERA5/HOURLY collection (pixelSize 27830).
TRUTH = {
    "CHIRPS_DAILY": (
        "UCSB-CHG/CHIRPS/DAILY",
        "EPSG:4326",
        [0.05, 0, -180, 0, -0.05, 50],
    ),
    "NASA_GPM_L3_IMERG_V07": (
        "NASA/GPM_L3/IMERG_V07",
        "EPSG:4326",
        [0.1, 0, -180, 0, -0.1, 90],
    ),
    "ERA5_LAND_DAILY_AGGR": (
        "ECMWF/ERA5_LAND/DAILY_AGGR",
        "EPSG:4326",
        [0.1, 0, -180.05, 0, -0.1, 90.05],
    ),
    "ERA5_LAND_HOURLY": (
        "ECMWF/ERA5_LAND/HOURLY",
        "EPSG:4326",
        [0.1, 0, -180.05, 0, -0.1, 90.05],
    ),
    "ERA5_HOURLY": (
        "ECMWF/ERA5/HOURLY",
        "EPSG:4326",
        # Plan row reconciled 2026-09-10 (decisions.md): original [0.25, 0,
        # -180, 0, -0.25, 90] omitted the half-pixel PixelIsArea offset the
        # plan's own ERA5-Land rows use; live GEE value confirmed independently.
        [0.25, 0, -180.125, 0, -0.25, 90.125],
    ),
    "NASA_SMAP_SPL4SMGP_008": (
        "NASA/SMAP/SPL4SMGP/008",
        "EPSG:4326",
        # todo 13 (2026-09-10): natively EPSG:4326 despite the 9 km EASE-Grid
        # product spec; ~0.095 deg cells, x/y scales differ microscopically,
        # top edge 85.0445 (polar gap — no cells above ~85N, correct not a bug).
        [0.09516256938937351, 0, -180, 0, -0.09516149300142858, 85.0445018795655],
    ),
}

TRANSFORM_NAMES = ["a", "b", "c", "d", "e", "f"]


def probe(dataset, collection_id, exp_crs, exp_tf):
    first = ee.ImageCollection(collection_id).first()
    info = first.getInfo()
    image_id = info["id"]
    ms = info["properties"]["system:time_start"]
    # epoch arithmetic, not fromtimestamp: ERA5-Land starts 1950 (negative
    # epoch ms), which datetime.fromtimestamp rejects on Windows.
    date = (datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=ms)).strftime("%Y-%m-%d")
    try:
        proj = ee.Image(image_id).projection().getInfo()
        proj_source = "image"
    except ee.ee_exception.EEException as exc:
        # ERA5_LAND_HOURLY carries bands with differing projections; band 0 is
        # the native ERA5-Land grid and is what reduceRegion aggregates over.
        if "different projections" not in str(exc):
            raise
        proj = ee.Image(image_id).select(0).projection().getInfo()
        proj_source = "band 0 (image has mixed band projections)"
    live_crs = proj["crs"]
    live_tf = proj["transform"]

    record = {
        "dataset": dataset,
        "collection_id": collection_id,
        "image_id": image_id,
        "date": date,
        "live_crs": live_crs,
        "live_transform": live_tf,
        "projection_source": proj_source,
        "expected_crs": exp_crs,
        "expected_transform": exp_tf,
        "match": True,
    }
    if live_crs != exp_crs:
        record["match"] = False
        print(f"MISMATCH {dataset} field=crs live={live_crs} expected={exp_crs}")
    for name, live, exp in zip(TRANSFORM_NAMES, live_tf, exp_tf):
        if abs(live - exp) > EPS:
            record["match"] = False
            print(f"MISMATCH {dataset} field=transform.{name} live={live} expected={exp}")
    if record["match"]:
        print(f"MATCH {dataset}")
    return record


RECONCILIATION_NOTE = (
    "ERA5_HOURLY plan row corrected 2026-09-10 (see .omo/notepads/pixel-grid-viz/"
    "decisions.md): [0.25, 0, -180, 0, -0.25, 90] -> [0.25, 0, -180.125, 0, "
    "-0.25, 90.125]; original omitted the half-pixel PixelIsArea offset the "
    "plan's own ERA5-Land rows use. Live GEE value confirmed independently."
)


def main():
    ee.Initialize(project=PROJECT)
    print(f"epsilon: transform value matches iff abs(live - expected) <= {EPS}")
    records = [probe(ds, *truth) for ds, truth in TRUTH.items()]
    all_match = all(r["match"] for r in records)
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(
        json.dumps(
            {
                "epsilon": EPS,
                "project": PROJECT,
                "note": RECONCILIATION_NOTE,
                "all_match": all_match,
                "datasets": records,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"TASK-2 RESULT: {'PASS' if all_match else 'FAIL'}")
    return 0 if all_match else 1


if __name__ == "__main__":
    sys.exit(main())
