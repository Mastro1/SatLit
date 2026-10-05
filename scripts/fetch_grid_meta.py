"""Fetch live GEE grid metadata (crs + transform) for one catalog entry.

Pixel-grid-viz todo 16: the paste-ready step of the new-dataset pipeline.
Probe machinery is IMPORTED from the existing scripts (no copy-paste):
  load_project, first_image, live_projection <- scripts/probe_projection.py
  assert_additive                             <- scripts/add_satellite.py

  python scripts/fetch_grid_meta.py --id NASA_SMAP_SPL4SMGP_008            # print only
  python scripts/fetch_grid_meta.py --id NASA_SMAP_SPL4SMGP_008 --write    # insert keys

--write inserts "crs"/"transform" right after the entry's "pixelSize" line,
additive-only (difflib-proven), then re-checks the freshly written keys within
the same invocation. --write is REFUSED (loud, non-zero) when the probe fails,
the live transform is rotated (|b| or |d| > 1e-7), or the entry already carries
crs/transform — remove them manually first, never overwrite.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ee

from add_satellite import assert_additive
from probe_projection import EPS, first_image, live_projection, load_project

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config" / "satellites.json"
ROTATION_EPS = 1e-7


def find_entry(config, sat_id):
    for section in ("satellites", "masks"):
        for entry in config.get(section, []):
            if isinstance(entry, dict) and entry.get("id") == sat_id:
                return section, entry
    return None, None


def insert_after_pixelsize(text, sat_id, crs, transform):
    """New text with the two keys inserted after <sat_id>'s "pixelSize" line.
    Raises ValueError if the anchor line cannot be located unambiguously."""
    lines = text.splitlines(True)
    in_block = False
    anchor = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == f'"id": "{sat_id}",':
            in_block = True
        elif in_block and stripped.startswith('"id":'):
            break  # next entry started; pixelSize never found inside the block
        if in_block and stripped.startswith('"pixelSize":'):
            anchor = i
            break
    if anchor is None:
        raise ValueError(f'no "pixelSize" line found inside the entry block of "{sat_id}"')
    indent = lines[anchor][: len(lines[anchor]) - len(lines[anchor].lstrip())]
    block = [f'{indent}"crs": {json.dumps(crs)},\n',
             f'{indent}"transform": {json.dumps(transform)},\n']
    return "".join(lines[: anchor + 1] + block + lines[anchor + 1 :])


def probe_live(collection_id):
    """(image_id, date, crs, transform, source) — imported probe machinery."""
    image_id, date = first_image(collection_id)
    proj, source = live_projection(image_id)
    return image_id, date, proj["crs"], list(proj["transform"]), source


def main():
    ap = argparse.ArgumentParser(description="Fetch live crs/transform for a satellites.json entry and optionally write them in.")
    ap.add_argument("--id", required=True, help="entry id in the catalog, e.g. NASA_SMAP_SPL4SMGP_008")
    ap.add_argument("--write", action="store_true",
                    help='insert "crs"+"transform" after the entry\'s pixelSize line')
    ap.add_argument("--project", default=None,
                    help="GEE project id (default: config/settings.toml [gee] project_id)")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG),
                    help=f"catalog path (default: {DEFAULT_CONFIG})")
    args = ap.parse_args()

    config_path = Path(args.config)
    try:
        # utf-8-sig: tolerates a leading BOM (Windows tools emit one by default);
        # --write below writes back plain utf-8 (no BOM), like the existing catalog.
        config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR config: cannot read/parse {config_path}: {exc}", file=sys.stderr)
        return 1
    section, entry = find_entry(config, args.id)
    if entry is None:
        print(f"ERROR: id '{args.id}' not found in {config_path} "
              "(searched 'satellites' and 'masks')", file=sys.stderr)
        return 1

    if args.write and (entry.get("crs") or entry.get("transform")):
        print(f"REFUSED --write: entry '{args.id}' already carries "
              f"crs={entry.get('crs')!r} transform={entry.get('transform')!r}. "
              "Remove them manually first — this script never overwrites.", file=sys.stderr)
        return 1

    try:
        ee.Initialize(project=load_project(args.project))
        image_id, date, crs, transform, source = probe_live(entry["ee_collection_name"])
    except ee.ee_exception.EEException as exc:
        print(f"REFUSED: probe failed for '{args.id}' "
              f"(collection '{entry['ee_collection_name']}'): {exc}", file=sys.stderr)
        return 1

    print(f"image:    {image_id}")
    print(f"date:     {date}")
    print(f"crs:      {crs}")
    print(f"transform: {transform}")
    print(f"projection source: {source}")
    print("paste-ready JSON snippet:")
    print(json.dumps({"crs": crs, "transform": transform}, indent=2))

    if max(abs(transform[1]), abs(transform[3])) > ROTATION_EPS:
        print(f"NOTE: rotation present (b={transform[1]}, d={transform[3]}, "
              f"threshold {ROTATION_EPS}) — grid overlay does not support rotated grids",
              file=sys.stderr)
        if args.write:
            print("REFUSED --write: rotated live transform", file=sys.stderr)
            return 1

    if not args.write:
        print(f"PRINT-ONLY — {config_path} not modified (pass --write to insert)")
        return 0

    old_text = config_path.read_text(encoding="utf-8")
    try:
        new_text = insert_after_pixelsize(old_text, args.id, crs, transform)
    except ValueError as exc:
        print(f"ABORT --write: {exc} — nothing written", file=sys.stderr)
        return 1
    if not assert_additive(old_text, new_text):
        print("ABORT --write: edit is not a single contiguous insertion — nothing written",
              file=sys.stderr)
        return 1

    original_bytes = config_path.read_bytes()
    with open(config_path, "w", encoding="utf-8", newline="") as fh:
        fh.write(new_text)

    # Post-write check without a second GEE round-trip: re-read the two keys
    # from disk and compare to the values just written (MATCH iff equal within
    # the probe's epsilon).
    stored = json.loads(config_path.read_text(encoding="utf-8"))
    s_entry = stored[section][[e["id"] for e in stored[section]].index(args.id)]
    match = s_entry["crs"] == crs and len(s_entry["transform"]) == 6 \
        and all(abs(a - b) <= EPS for a, b in zip(s_entry["transform"], transform))
    print(f"post-write probe: {'MATCH' if match else 'MISMATCH'} "
          f"(stored crs/transform vs written values, eps={EPS})")
    if not match:
        config_path.write_bytes(original_bytes)
        print("post-write probe FAILED — config restored", file=sys.stderr)
        return 1
    print(f"WROTE crs+transform into '{args.id}' ({section}) in {config_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
