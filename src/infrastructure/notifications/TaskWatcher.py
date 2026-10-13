"""
Desktop notifications for GEE tasks submitted from SatLit.

One background thread polls only the operations SatLit started
(ee.data.getOperation per pending task) and sleeps on an Event while
nothing is pending, so it costs nothing between exports.
"""
import logging
import shutil
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

import ee

logger = logging.getLogger(__name__)

ASSETS = Path(__file__).parent.parent.parent.parent / "assets"
MAX_FAILS = 5

TITLES = {
    "SUCCEEDED": "✅ Task completed",
    "FAILED": "❌ Task failed",
    "CANCELLED": "⚠️ Task cancelled",
}


def _send(title: str, message: str):
    try:
        if sys.platform == "win32":
            from plyer import notification
            notification.notify(
                title=title, message=message, app_name="SatLit",
                app_icon=str(ASSETS / "favicon.ico"), timeout=10,
            )
        elif sys.platform == "darwin":
            # Text passed via argv: no AppleScript quoting/injection issues.
            subprocess.run(
                ["osascript", "-e", "on run argv",
                 "-e", "display notification (item 2 of argv) with title (item 1 of argv)",
                 "-e", "end run", title, message],
                check=False, timeout=10,
            )
        elif shutil.which("notify-send"):
            subprocess.run(
                ["notify-send", "-a", "SatLit", "-i", str(ASSETS / "favicon.png"),
                 title, message],
                check=False, timeout=10,
            )
    except Exception as e:
        logger.warning("Desktop notification failed: %s", e)


def desktop_notify(title: str, message: str):
    """Fire-and-forget: a blocking backend never stalls the poll loop."""
    threading.Thread(target=_send, args=(title, message), daemon=True).start()


def format_duration(start_iso, end_iso) -> str:
    try:
        start = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
        end = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        return "unknown"
    total = max(int((end - start).total_seconds()), 0)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    parts = [f"{v}{u}" for v, u in ((h, "h"), (m, "m")) if v]
    if s or not parts:
        parts.append(f"{s}s")
    return " ".join(parts)


class TaskWatcher:
    def __init__(self, fetch=ee.data.getOperation, notify=desktop_notify):
        self._fetch = fetch
        self._notify = notify
        self._pending = {}  # operation name -> {"desc", "announce", "fails"}
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._thread = None
        self.enabled = True
        self.interval = 60

    def watch(self, name: str, description: str, announce: bool = True):
        """Track an operation. announce=False = seeded from history: a task
        already finished on first poll is dropped without a banner."""
        if not self.enabled or not name:
            return
        with self._lock:
            self._pending[name] = {"desc": description, "announce": announce, "fails": 0}
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, daemon=True)
                self._thread.start()
        self._wake.set()

    def configure(self, enabled: bool, interval: int):
        self.enabled = bool(enabled)
        self.interval = int(interval)
        if not self.enabled:
            with self._lock:
                self._pending.clear()
        self._wake.set()

    def poll_once(self):
        with self._lock:
            items = list(self._pending.items())
        for name, entry in items:
            try:
                op = self._fetch(name)
            except Exception as e:
                entry["fails"] += 1
                logger.warning("Task poll failed (%s/%s) for %s: %s",
                               entry["fails"], MAX_FAILS, name, e)
                if entry["fails"] >= MAX_FAILS:
                    self._drop(name)
                continue

            entry["fails"] = 0
            if not op.get("done"):
                entry["announce"] = True
                continue

            if entry["announce"]:
                meta = op.get("metadata", {})
                state = meta.get("state", "")
                lines = [entry["desc"] or meta.get("description", "GEE task"),
                         f"Duration: {format_duration(meta.get('startTime'), meta.get('updateTime'))}"]
                error = op.get("error", {}).get("message")
                if error:
                    lines.append(error[:200])
                self._notify(TITLES.get(state, f"Task {state.lower() or 'finished'}"),
                             "\n".join(lines))
            self._drop(name)

    def _drop(self, name: str):
        with self._lock:
            self._pending.pop(name, None)

    def _run(self):
        while True:
            if not self._pending:
                self._wake.wait()
                self._wake.clear()
                continue
            # configure() sets the event so a new interval applies at once.
            if self._wake.wait(self.interval):
                self._wake.clear()
                continue
            try:
                self.poll_once()
            except Exception as e:
                logger.warning("Task watcher error: %s", e)
