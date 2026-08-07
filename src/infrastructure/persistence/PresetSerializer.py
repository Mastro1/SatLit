"""Serialize and validate extraction presets for share / import."""
from __future__ import annotations

import copy
import json
import re
from typing import Any

PRESET_TYPE = "gee_extraction_preset"
SUPPORTED_SCHEMA_VERSION = 1

CONFIG_KEYS = (
    "satellite",
    "bands",
    "reducers",
    "geometry_source",
    "selected_points",
    "gadm_selection",
    "gadm_regions",
    "dates",
    "export_method",
    "output_format",
    "custom_filename",
    "shapefile_required",
    "num_points",
)

STRIP_FROM_CONFIG = {
    "task_id",
    "job_id",
    "timestamp",
    "uploaded_shapefile",
    "preset_id",
    "name",
    "notes",
    "created_at",
    "updated_at",
    "schema_version",
    "type",
    "config",
}


class PresetValidationError(ValueError):
    """Raised when a share payload cannot be turned into a preset."""


def sanitize_filename(name: str) -> str:
    """Build a safe download filename stem from a preset name."""
    cleaned = re.sub(r"[^\w\s\-]", "", name or "preset", flags=re.UNICODE)
    cleaned = re.sub(r"\s+", "_", cleaned.strip())
    return cleaned[:80] or "preset"


def config_from_history_or_session(entry: dict) -> dict:
    """
    Build a portable preset config from a history entry or session snapshot.

    Never keeps absolute shapefile paths. Marks shapefile_required when ROI is Shapefile.
    """
    if not isinstance(entry, dict):
        raise PresetValidationError("Config must be an object.")

    source = copy.deepcopy(entry)
    # Unwrap nested config if already a share payload piece
    if "config" in source and isinstance(source["config"], dict) and "satellite" not in source:
        source = source["config"]

    geometry_source = source.get("geometry_source")
    has_shapefile_path = bool(source.get("uploaded_shapefile"))
    shapefile_required = bool(source.get("shapefile_required")) or geometry_source == "Shapefile" or has_shapefile_path

    config: dict[str, Any] = {}
    for key in CONFIG_KEYS:
        if key == "shapefile_required":
            continue
        if key in source:
            config[key] = source[key]

    config.pop("uploaded_shapefile", None)

    if "satellite" not in config or not config["satellite"]:
        raise PresetValidationError("A preset needs a satellite.")

    if "bands" not in config or not isinstance(config.get("bands"), list):
        config["bands"] = list(config.get("bands") or [])

    if "reducers" not in config or not isinstance(config.get("reducers"), dict):
        config["reducers"] = dict(config.get("reducers") or {})

    if "selected_points" not in config or not isinstance(config.get("selected_points"), list):
        config["selected_points"] = list(config.get("selected_points") or [])

    if "gadm_selection" in config and isinstance(config["gadm_selection"], dict):
        config["gadm_selection"] = {
            k: v for k, v in config["gadm_selection"].items() if k != "gdf"
        }

    if not geometry_source:
        if config["selected_points"]:
            geometry_source = "Points"
        elif config.get("gadm_selection"):
            geometry_source = "GADM"
        elif shapefile_required:
            geometry_source = "Shapefile"
        else:
            geometry_source = "Points"
        config["geometry_source"] = geometry_source

    if shapefile_required:
        config["geometry_source"] = "Shapefile"
        config["shapefile_required"] = True
        config["selected_points"] = []
    else:
        config["shapefile_required"] = False

    if "dates" not in config or not isinstance(config.get("dates"), dict):
        config["dates"] = {}

    if "export_method" not in config:
        config["export_method"] = "Drive"
    if "output_format" not in config:
        config["output_format"] = "CSV"

    config["num_points"] = len(config.get("selected_points") or [])

    # Drop any leftover non-portable keys
    for key in list(config.keys()):
        if key in STRIP_FROM_CONFIG or key not in CONFIG_KEYS:
            if key not in CONFIG_KEYS:
                config.pop(key, None)

    return config


def to_share_payload(preset: dict) -> dict:
    """Build the portable JSON package for download / clipboard."""
    config = config_from_history_or_session(preset.get("config") or preset)
    return {
        "schema_version": SUPPORTED_SCHEMA_VERSION,
        "type": PRESET_TYPE,
        "name": preset.get("name") or "Untitled preset",
        "notes": preset.get("notes") or "",
        "created_at": preset.get("created_at"),
        "updated_at": preset.get("updated_at"),
        "config": config,
    }


def to_share_json(preset: dict, indent: int = 2) -> str:
    return json.dumps(to_share_payload(preset), indent=indent)


def _looks_like_history_entry(data: dict) -> bool:
    if data.get("type") == PRESET_TYPE:
        return False
    if "satellite" in data and ("bands" in data or "geometry_source" in data):
        return True
    return False


def from_share_payload(raw: Any) -> dict:
    """
    Parse upload/paste into a normalized draft:
    { name, notes, config, warnings: list[str] }

    Raises PresetValidationError on hard failures.
    """
    warnings: list[str] = []

    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")

    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            raise PresetValidationError(
                "That doesn't look like a preset. Export again from Presets → Share, or paste the full JSON."
            )
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise PresetValidationError(
                "That doesn't look like a preset. Export again from Presets → Share, or paste the full JSON."
            ) from exc
    elif isinstance(raw, dict):
        data = raw
    else:
        raise PresetValidationError(
            "That doesn't look like a preset. Export again from Presets → Share, or paste the full JSON."
        )

    if not isinstance(data, dict):
        raise PresetValidationError(
            "That doesn't look like a preset. Export again from Presets → Share, or paste the full JSON."
        )

    name = data.get("name") or "Imported preset"
    notes = data.get("notes") or ""

    if data.get("type") == PRESET_TYPE or "config" in data:
        version = data.get("schema_version", SUPPORTED_SCHEMA_VERSION)
        if not isinstance(version, int):
            try:
                version = int(version)
            except (TypeError, ValueError) as exc:
                raise PresetValidationError(
                    "This preset needs a newer app version."
                ) from exc
        if version > SUPPORTED_SCHEMA_VERSION:
            raise PresetValidationError(
                "This preset needs a newer app version."
            )
        if data.get("type") not in (None, PRESET_TYPE) and "config" not in data:
            raise PresetValidationError(
                "That doesn't look like a preset. Export again from Presets → Share, or paste the full JSON."
            )
        config_src = data.get("config")
        if not isinstance(config_src, dict):
            if _looks_like_history_entry(data):
                config_src = data
                warnings.append("Converted a history-style entry into a preset.")
            else:
                raise PresetValidationError(
                    "That doesn't look like a preset. Export again from Presets → Share, or paste the full JSON."
                )
        config = config_from_history_or_session(config_src)
    elif _looks_like_history_entry(data):
        warnings.append("Converted a history-style entry into a preset.")
        config = config_from_history_or_session(data)
        if not name or name == "Imported preset":
            sat = config.get("satellite", "preset")
            dates = config.get("dates") or {}
            start = dates.get("start_year", "")
            end = dates.get("end_year", "")
            name = f"{sat}_{start}_{end}".strip("_")
    else:
        raise PresetValidationError(
            "That doesn't look like a preset. Export again from Presets → Share, or paste the full JSON."
        )

    if config.get("shapefile_required"):
        warnings.append(
            "This preset uses a shapefile. Paths aren't shared, so please choose your local file under File Import."
        )

    return {
        "name": str(name).strip() or "Imported preset",
        "notes": str(notes),
        "config": config,
        "warnings": warnings,
    }


def preview_summary(name: str, config: dict) -> dict:
    """Compact fields for the import preview UI."""
    dates = config.get("dates") or {}
    bands = config.get("bands") or []
    return {
        "name": name,
        "satellite": config.get("satellite"),
        "bands_count": len(bands),
        "bands": bands,
        "geometry_source": config.get("geometry_source"),
        "date_range": f"{dates.get('start_year', '?')}–{dates.get('end_year', '?')}",
        "shapefile_required": bool(config.get("shapefile_required")),
    }


def default_name_from_config(config: dict) -> str:
    sat = config.get("satellite") or "preset"
    dates = config.get("dates") or {}
    start = dates.get("start_year")
    end = dates.get("end_year")
    if start and end:
        return f"{sat}_{start}_{end}"
    return str(sat)
