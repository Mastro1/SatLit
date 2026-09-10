"""Fetch live GEE grid metadata (crs + transform) for one catalog entry.

Pixel-grid-viz todo 16: the paste-ready step of the new-dataset pipeline.
Probe machinery is IMPORTED from the existing scripts (no copy-paste):
  load_project  <- scripts/probe_projection.py (settings.toml / --project)
  first_image   <- scripts/verify_grid.py      (epoch arithmetic)
  live_projection <- scripts/verify_grid.py    (.select(0) mixed-projection fallback)

  python scripts/fetch_grid_meta.py --id NASA_SMAP_SPL4SMGP_008            # print only
  python scripts/fetch_grid_meta.py --id NASA_SMAP_SPL4SMGP_008 --write    # insert keys

--write inserts "crs"/"transform" right after the entry's "pixelSize" line,
additive-only (difflib-proven, git-numstat-checked), then re-probes to confirm
MATCH within the same invocation. --write is REFUSED (loud, non-zero) when the
probe fails, the live transform is rotated (|b| or |d| > 1e-7), or the entry
already carries crs/transform — remove them manually first, never overwrite.
"""
import argparse
import difflib
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ee

from probe_projection import EPS, load_project
from verify_grid import first_image, live_projection

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


def assert_additive(old_text, new_text):
    ops = [tag for tag, *_ in difflib.SequenceMatcher(None, old_text.splitlines(True),
                                                      new_text.splitlines(True)).get_opcodes()]
    return ops.count("insert") == 1 and "replace" not in ops and "delete" not in ops


def git_numstat(path):
    out = subprocess.run(["git", "diff", "--numstat", "--", str(path)],
                         capture_output=True, text=True, cwd=ROOT).stdout.strip()
    return (int(a), int(d)) if out and out.split()[0] != "-" else (None, None)


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

    was_clean = subprocess.run(["git", "status", "--porcelain", "--", str(config_path)],
                               capture_output=True, text=True, cwd=ROOT).stdout.strip() == ""
    original_bytes = config_path.read_bytes()
    with open(config_path, "w", encoding="utf-8", newline="") as fh:
        fh.write(new_text)

    if was_clean:
        added, deleted = git_numstat(config_path)
        if deleted:  # None/0 = no deletions (None: not a tracked file, e.g. a temp copy)
            config_path.write_bytes(original_bytes)
            print(f"ABORT --write: git diff shows {deleted} deletion(s) — rolled back",
                  file=sys.stderr)
            return 1
        if added is not None:
            print(f"git diff --numstat on {config_path}: +{added} -{deleted} (pure additions)")

    # Re-run the probe path against the freshly written catalog: MATCH iff the
    # stored keys equal the live projection within the probe's epsilon.
    stored = json.loads(config_path.read_text(encoding="utf-8"))
    s_entry = dict(stored[section][[e["id"] for e in stored[section]].index(args.id)])
    _, _, live_crs, live_tf, _ = probe_live(entry["ee_collection_name"])
    match = s_entry["crs"] == live_crs and len(s_entry["transform"]) == 6 \
        and all(abs(a - b) <= EPS for a, b in zip(s_entry["transform"], live_tf))
    print(f"post-write probe: {'MATCH' if match else 'MISMATCH'} "
          f"(stored crs/transform vs live, eps={EPS})")
    if not match:
        config_path.write_bytes(original_bytes)
        print("post-write probe FAILED — config restored", file=sys.stderr)
        return 1
    print(f"WROTE crs+transform into '{args.id}' ({section}) in {config_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
