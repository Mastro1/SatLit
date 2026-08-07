import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional


class HistoryManager:
    def __init__(self, cache_folder: str = "./.cache/"):
        self.cache_folder = Path(cache_folder)
        self.history_file = self.cache_folder / "history.json"

        # Ensure cache directory exists
        self.cache_folder.mkdir(parents=True, exist_ok=True)

        self._history = self._load_history()

    def _load_history(self) -> list:
        """Loads history from JSON file."""
        if not self.history_file.exists():
            return []

        try:
            with open(self.history_file, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"Error loading history: {e}")
            return []

    def add_entry(self, entry: dict):
        """Adds a new entry to the history."""
        # Add metadata
        entry["timestamp"] = datetime.now().isoformat()
        entry["job_id"] = str(uuid.uuid4())

        self._history.insert(0, entry)  # Prepend to keep newest first
        self._save_history()

    def get_history(self) -> list:
        """Returns the full history list."""
        return self._history

    def get_cache_stats(self) -> dict:
        """Returns size and timestamp range for the history cache file."""
        file_size = self.history_file.stat().st_size if self.history_file.exists() else 0
        timestamps = []
        for entry in self._history:
            parsed = self._parse_timestamp(entry.get("timestamp"))
            if parsed is not None:
                timestamps.append(parsed)

        return {
            "entry_count": len(self._history),
            "file_size_bytes": file_size,
            "oldest_timestamp": min(timestamps).isoformat() if timestamps else None,
            "newest_timestamp": max(timestamps).isoformat() if timestamps else None,
        }

    def clear_all(self) -> int:
        """Removes all history entries. Returns the number removed."""
        removed = len(self._history)
        self._history = []
        self._save_history()
        return removed

    def clear_older_than(self, cutoff: datetime) -> int:
        """Removes entries older than cutoff. Keeps unparseable timestamps. Returns removed count."""
        kept = []
        removed = 0
        for entry in self._history:
            parsed = self._parse_timestamp(entry.get("timestamp"))
            if parsed is None or parsed >= cutoff:
                kept.append(entry)
            else:
                removed += 1

        self._history = kept
        self._save_history()
        return removed

    @staticmethod
    def _parse_timestamp(value) -> Optional[datetime]:
        if not value or not isinstance(value, str):
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    def _save_history(self):
        """Attributes persistent storage."""
        try:
            with open(self.history_file, "w") as f:
                json.dump(self._history, f, indent=2)
        except IOError as e:
            print(f"Error saving history: {e}")
