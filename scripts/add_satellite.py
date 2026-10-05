"""Validate and insert a new satellite/dataset entry into satellites.json.

Safe pipeline for new datasets (pixel-grid-viz todo 16):

  python scripts/add_satellite.py --entry new.json --dry-run   # validate only
  python scripts/add_satellite.py --entry new.json             # insert + revalidate

Every failure exits non-zero naming the offending field. The insert is
additive-only by construction:
  - the file is re-serialized with a house-style encoder that reproduces the
    existing formatting (numeric lists inline, 2-space indent, raw UTF-8);
  - the new text must equal old text + one contiguous inserted block
    (difflib-proven via assert_additive) or the write is aborted — never a
    silent reformat.
"""
import argparse
import difflib
import json
import math
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config" / "satellites.json"

BAND_KEYS = {"name", "units", "min", "max", "description"}
SECTION_OPTIONAL = {
    "satellites": {"isHourly", "cadence", "crs", "transform", "endDate", "filters"},
    "masks": {"endDate", "filters"},
}
REQUIRED = {"id", "name", "ee_collection_name", "description", "website",
            "startDate", "pixelSize", "bands"}


def is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def date_error(v, field):
    """None if v is a real calendar date 'YYYY-MM-DD', else the reason."""
    if not isinstance(v, str):
        return f"{field}: must be a 'YYYY-MM-DD' string, got {v!r}"
    try:
        # roundtrip equality keeps the zero-padding strict (strptime alone
        # would accept '2021-1-1'); '2021-02-30' raises ValueError.
        if datetime.strptime(v, "%Y-%m-%d").strftime("%Y-%m-%d") != v:
            raise ValueError("expected zero-padded 'YYYY-MM-DD'")
    except ValueError as exc:
        return f"{field}: not a real calendar date: {v!r} ({exc})"
    return None


def nonempty_str(v, field):
    return None if isinstance(v, str) and v else f"{field}: must be a non-empty string, got {v!r}"


def unit_str(v, field):
    """units may be '' (dimensionless) but must be a string."""
    return None if isinstance(v, str) else f"{field}: must be a string, got {v!r}"


def validate_entry(entry, section, config):
    """Return a list of error strings (empty = valid). Uniqueness is checked
    against BOTH sections of the loaded config."""
    errors = []
    err = lambda msg: errors.append(msg)  # noqa: E731

    if not isinstance(entry, dict):
        return [f"entry: must be a JSON object, got {type(entry).__name__}"]

    optional = SECTION_OPTIONAL[section]
    unknown = sorted(set(entry) - REQUIRED - optional)
    if unknown:
        err(f"unknown key(s) {unknown} not allowed in section '{section}' "
            f"(typo? allowed optional keys: {sorted(REQUIRED | optional)})")
    for key in sorted(REQUIRED - set(entry)):
        err(f"{key}: required key is missing")

    for key in ("id", "name", "ee_collection_name", "website"):
        if key in entry and (e := nonempty_str(entry[key], key)):
            err(e)
    if "description" in entry and (e := nonempty_str(entry["description"], "description")):
        err(e)
    if "startDate" in entry and (e := date_error(entry["startDate"], "startDate")):
        err(e)
    if "endDate" in entry and (e := date_error(entry["endDate"], "endDate")):
        err(e)
    if "cadence" in entry and (e := nonempty_str(entry["cadence"], "cadence")):
        err(e)
    if "isHourly" in entry and not isinstance(entry["isHourly"], bool):
        err(f"isHourly: must be a bool, got {entry['isHourly']!r}")

    if "pixelSize" in entry:
        if not is_int(entry["pixelSize"]) or entry["pixelSize"] <= 0:
            err(f"pixelSize: must be a positive integer, got {entry['pixelSize']!r} "
                "(bools are not integers)")

    if "bands" in entry:
        bands = entry["bands"]
        if not isinstance(bands, list) or not bands:
            err(f"bands: must be a non-empty list, got {bands!r}")
        else:
            for i, band in enumerate(bands):
                if not isinstance(band, dict):
                    err(f"bands[{i}]: must be an object, got {type(band).__name__}")
                    continue
                bad = sorted(set(band) - BAND_KEYS)
                if bad:
                    err(f"bands[{i}]: unknown key(s) {bad} (allowed: {sorted(BAND_KEYS)})")
                for key in ("name", "description"):
                    if key in band and (e := nonempty_str(band[key], f"bands[{i}].{key}")):
                        err(e)
                if "units" in band and (e := unit_str(band["units"], f"bands[{i}].units")):
                    err(e)
                for key in ("min", "max"):
                    if key in band and not is_num(band[key]):
                        err(f"bands[{i}].{key}: must be a number, got {band[key]!r}")

    if "filters" in entry:
        filters = entry["filters"]
        if not isinstance(filters, list) or not all(isinstance(f, dict) for f in filters):
            err(f"filters: must be a list of objects, got {filters!r}")

    has_crs, has_tf = "crs" in entry, "transform" in entry
    if has_crs != has_tf:
        err("crs/transform: co-required — provide both or neither "
            f"(crs={'present' if has_crs else 'absent'}, "
            f"transform={'present' if has_tf else 'absent'})")
    if has_crs and (e := nonempty_str(entry["crs"], "crs")):
        err(e)
    if has_tf:
        tf = entry["transform"]
        if not isinstance(tf, list) or len(tf) != 6 or not all(is_num(v) for v in tf) \
                or not all(math.isfinite(v) for v in tf):
            err(f"transform: must be a list of 6 finite numbers, got {tf!r}")

    for key in ("id", "ee_collection_name"):
        if key not in entry:
            continue
        for sec in config:
            for other in config[sec]:
                if not isinstance(other, dict) or other.get(key) != entry[key]:
                    continue
                if key == "id":
                    err(f"id: '{entry['id']}' already exists in section '{sec}' — ids must "
                        "be unique across satellites AND masks")
                else:
                    err(f"ee_collection_name: '{entry['ee_collection_name']}' already used by "
                        f"'{other.get('id')}' in section '{sec}' — must be unique")

    return errors


def dump_catalog(data):
    """House-style serializer: matches the existing satellites.json byte-for-byte
    (2-space indent, numeric lists inline, raw UTF-8, trailing newline)."""
    def enc(v, indent):
        pad, pad2 = "  " * indent, "  " * (indent + 1)
        if isinstance(v, dict):
            if not v:
                return "{}"
            items = [f"{pad2}{json.dumps(k, ensure_ascii=False)}: {enc(val, indent + 1)}"
                     for k, val in v.items()]
            return "{\n" + ",\n".join(items) + f"\n{pad}}}"
        if isinstance(v, list):
            if not v:
                return "[]"
            if all(is_num(item) for item in v):
                return "[" + ", ".join(enc(item, indent) for item in v) + "]"
            items = [f"{pad2}{enc(item, indent + 1)}" for item in v]
            return "[\n" + ",\n".join(items) + f"\n{pad}]"
        return json.dumps(v, ensure_ascii=False)

    return enc(data, 0) + "\n"


def assert_additive(old_text, new_text):
    """True iff new_text == old_text + exactly one contiguous inserted block."""
    ops = [tag for tag, *_ in difflib.SequenceMatcher(None, old_text.splitlines(True),
                                                      new_text.splitlines(True)).get_opcodes()]
    return ops.count("insert") == 1 and "replace" not in ops and "delete" not in ops


def main():
    ap = argparse.ArgumentParser(description="Validate and insert a new dataset entry into satellites.json.")
    ap.add_argument("--entry", required=True, help="path to the single-entry JSON file")
    ap.add_argument("--section", choices=sorted(SECTION_OPTIONAL), default="satellites",
                    help="which top-level array to insert into (default: satellites)")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG),
                    help=f"catalog path (default: {DEFAULT_CONFIG})")
    ap.add_argument("--dry-run", action="store_true",
                    help="validate only; do not write the config")
    args = ap.parse_args()

    config_path = Path(args.config)
    try:
        # utf-8-sig: tolerates a leading BOM (Windows tools emit one by default)
        # and decodes BOM-less files identically. Writes below stay plain utf-8.
        entry = json.loads(Path(args.entry).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR entry file: cannot read/parse {args.entry}: {exc}", file=sys.stderr)
        return 1
    try:
        config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR config: cannot read/parse {config_path}: {exc}", file=sys.stderr)
        return 1

    errors = validate_entry(entry, args.section, config)
    if errors:
        print(f"VALIDATION FAILED for '{entry.get('id', '<unnamed>')}' "
              f"(section '{args.section}') — {len(errors)} error(s):")
        for e in errors:
            print(f"  ERROR {e}")
        return 1
    print(f"VALIDATION OK for '{entry['id']}' (section '{args.section}'):")
    print(f"  required keys present + typed: {sorted(REQUIRED)}")
    print(f"  optional keys within whitelist: {sorted(set(entry) - REQUIRED) or '(none)'}")
    print(f"  startDate={entry['startDate']} real calendar date; "
          f"pixelSize={entry['pixelSize']} positive int; {len(entry['bands'])} band(s) schema OK")
    print(f"  id + ee_collection_name unique across both sections of {config_path}")

    if args.dry_run:
        print(f"DRY-RUN OK — no changes written to {config_path}")
        return 0

    old_text = config_path.read_text(encoding="utf-8")
    new_text = dump_catalog({**config, args.section: [*config[args.section], entry]})
    if not assert_additive(old_text, new_text):
        print("ABORT: serialization would reformat existing content "
              "(diff is not a single contiguous insertion) — nothing written", file=sys.stderr)
        return 1

    original_bytes = config_path.read_bytes()
    with open(config_path, "w", encoding="utf-8", newline="") as fh:
        fh.write(new_text)

    reloaded = json.loads(config_path.read_text(encoding="utf-8"))
    inserted = reloaded[args.section][-1]
    # Re-validate the persisted entry; drop it by identity from the scan so the
    # uniqueness check compares against all OTHER entries, not itself.
    rest = dict(reloaded)
    rest[args.section] = [e for e in reloaded[args.section] if e is not inserted]
    errors = validate_entry(inserted, args.section, rest)
    if errors:
        config_path.write_bytes(original_bytes)
        print(f"ABORT: post-insert validation failed {errors} — config restored", file=sys.stderr)
        return 1
    print(f"INSERTED '{entry['id']}' into '{args.section}' of {config_path} "
          f"({len(reloaded[args.section])} entries in section); post-insert validation OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
