"""Local library of reusable extraction presets (.cache/presets.json)."""
from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

import json

from src.infrastructure.persistence.PresetSerializer import (
    PresetValidationError,
    config_from_history_or_session,
    default_name_from_config,
)


class PresetManager:
    def __init__(self, cache_folder: str = "./.cache/"):
        self.cache_folder = Path(cache_folder)
        self.presets_file = self.cache_folder / "presets.json"
        self.cache_folder.mkdir(parents=True, exist_ok=True)
        self._presets = self._load_presets()

    def _load_presets(self) -> list:
        if not self.presets_file.exists():
            return []
        try:
            with open(self.presets_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                return []
            return data
        except (json.JSONDecodeError, OSError) as e:
            print(f"Error loading presets: {e}")
            return []

    def _save_presets(self):
        try:
            with open(self.presets_file, "w", encoding="utf-8") as f:
                json.dump(self._presets, f, indent=2)
        except OSError as e:
            print(f"Error saving presets: {e}")

    def list_presets(self) -> list:
        """Return presets sorted A–Z by name (case-insensitive)."""
        return sorted(
            self._presets,
            key=lambda p: (p.get("name") or "").lower(),
        )

    def get(self, preset_id: str) -> Optional[dict]:
        for preset in self._presets:
            if preset.get("preset_id") == preset_id:
                return preset
        return None

    def get_by_name(self, name: str) -> Optional[dict]:
        for preset in self._presets:
            if preset.get("name") == name:
                return preset
        return None

    def unique_name(self, desired: str) -> str:
        """Return desired name, or Name (2), Name (3), … if taken."""
        base = (desired or "").strip() or "Untitled preset"
        existing = {p.get("name") for p in self._presets}
        if base not in existing:
            return base
        n = 2
        while f"{base} ({n})" in existing:
            n += 1
        return f"{base} ({n})"

    def add(
        self,
        name: str,
        config: dict,
        notes: str = "",
        *,
        make_unique_name: bool = True,
    ) -> dict:
        cleaned_name = (name or "").strip()
        if not cleaned_name:
            raise PresetValidationError("Please give your preset a name.")

        portable = config_from_history_or_session(config)
        final_name = self.unique_name(cleaned_name) if make_unique_name else cleaned_name
        if not make_unique_name and self.get_by_name(final_name):
            raise PresetValidationError(f'A preset named "{final_name}" already exists.')

        now = datetime.now().isoformat()
        preset = {
            "preset_id": str(uuid.uuid4()),
            "name": final_name,
            "notes": notes or "",
            "created_at": now,
            "updated_at": now,
            "config": portable,
        }
        self._presets.append(preset)
        self._save_presets()
        return preset

    def update(self, preset_id: str, *, name: Optional[str] = None, notes: Optional[str] = None, config: Optional[dict] = None) -> Optional[dict]:
        preset = self.get(preset_id)
        if not preset:
            return None
        if name is not None:
            cleaned = name.strip()
            if not cleaned:
                raise PresetValidationError("Please give your preset a name.")
            other = self.get_by_name(cleaned)
            if other and other.get("preset_id") != preset_id:
                cleaned = self.unique_name(cleaned)
            preset["name"] = cleaned
        if notes is not None:
            preset["notes"] = notes
        if config is not None:
            preset["config"] = config_from_history_or_session(config)
        preset["updated_at"] = datetime.now().isoformat()
        self._save_presets()
        return preset

    def delete(self, preset_id: str) -> bool:
        before = len(self._presets)
        self._presets = [p for p in self._presets if p.get("preset_id") != preset_id]
        if len(self._presets) == before:
            return False
        self._save_presets()
        return True

    def save_from_session_config(self, config: dict, name: str, notes: str = "") -> dict:
        return self.add(name=name, config=config, notes=notes)

    @staticmethod
    def suggest_name_from_entry(entry: dict) -> str:
        try:
            config = config_from_history_or_session(entry)
        except PresetValidationError:
            return "Untitled preset"
        return default_name_from_config(config)
