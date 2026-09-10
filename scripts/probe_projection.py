"""Live GEE projection proof for pixel-grid-viz todo 2.

Probes one image per gated dataset family and diffs the returned
projection against the catalog. The gated set, collections, and expected
crs/transform are derived at runtime from config/satellites.json (every
top-level satellite carrying BOTH a truthy crs and a 6-float transform) —
no truth table is hardcoded here. Exit 0 iff all MATCH.

Float comparison: a transform value matches iff abs(live - expected) <= 1e-9.
"""
import argparse
import json
import sys
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ee

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / ".omo/evidence/task-2-pixel-grid-viz.json"
SETTINGS = ROOT / "config" / "settings.toml"
CATALOG = ROOT / "config" / "satellites.json"
EPS = 1e-9

TRANSFORM_NAMES = ["a", "b", "c", "d", "e", "f"]


def load_project(override=None):
    """GEE project id: --project wins, else config/settings.toml [gee] project_id."""
    if override:
        return override
    with open(SETTINGS, "rb") as fh:
        project = tomllib.load(fh).get("gee", {}).get("project_id")
    if not project:
        raise SystemExit(
            f"missing/empty key 'project_id' in {SETTINGS} under [gee] — "
            "set it there or pass --project"
        )
    return project


def load_gated():
    """Gated probe set = catalog satellites with BOTH truthy crs and a
    6-number transform. Collections and expectations come from the same
    entries. Catalog order preserved."""
    gated = {}
    for sat in json.loads(CATALOG.read_text(encoding="utf-8"))["satellites"]:
        crs, tf = sat.get("crs"), sat.get("transform")
        if crs and isinstance(tf, list) and len(tf) == 6 and all(isinstance(v, (int, float)) for v in tf):
            gated[sat["id"]] = (sat["ee_collection_name"], crs, tf)
    if not gated:
        raise SystemExit(f"no gated datasets found in {CATALOG} (need crs + 6-float transform)")
    return gated


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
    ap = argparse.ArgumentParser(description="Probe live GEE projections vs the satellites.json catalog.")
    ap.add_argument("--project", default=None,
                    help="GEE project id (default: config/settings.toml [gee] project_id)")
    ap.add_argument("--tamper", metavar="DATASET:FIELD:SHIFT", default=None,
                    help="sensitivity check: shift the expected transform FIELD of DATASET by "
                         "SHIFT before diffing, e.g. ERA5_LAND_DAILY_AGGR:c:0.05 "
                         "(mirrors verify_grid.py --tamper-shift-c)")
    args = ap.parse_args()

    project = load_project(args.project)
    gated = load_gated()
    if args.tamper:
        parts = args.tamper.split(":")
        if len(parts) != 3:
            raise SystemExit(f"--tamper expects DATASET:FIELD:SHIFT, got '{args.tamper}'")
        ds, field, shift = parts
        if ds not in gated:
            raise SystemExit(f"--tamper dataset '{ds}' not in gated set {sorted(gated)}")
        if field not in TRANSFORM_NAMES:
            raise SystemExit(f"--tamper field '{field}' must be one of {TRANSFORM_NAMES}")
        gated[ds][2][TRANSFORM_NAMES.index(field)] += float(shift)
        print(f"tampered expected {ds} transform.{field} by {shift}")

    ee.Initialize(project=project)
    print(f"epsilon: transform value matches iff abs(live - expected) <= {EPS}")
    records = [probe(ds, *truth) for ds, truth in gated.items()]
    all_match = all(r["match"] for r in records)
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(
        json.dumps(
            {
                "epsilon": EPS,
                "project": project,
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
