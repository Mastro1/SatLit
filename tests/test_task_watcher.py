"""TaskWatcher: notify only on SatLit tasks finishing, no spam on restart."""
from src.infrastructure.notifications.TaskWatcher import (
    MAX_FAILS,
    TaskWatcher,
    format_duration,
)

RUNNING = {"done": False, "metadata": {"state": "RUNNING"}}
DONE = {
    "done": True,
    "metadata": {
        "state": "SUCCEEDED",
        "startTime": "2026-01-01T10:00:00.5Z",
        "updateTime": "2026-01-01T11:02:03.5Z",
    },
}
FAILED = {
    "done": True,
    "metadata": {"state": "FAILED"},
    "error": {"message": "Computation timed out."},
}


def make(ops):
    sent = []
    w = TaskWatcher(fetch=lambda name: ops[name], notify=lambda t, m: sent.append((t, m)))
    w._thread = object()  # tests drive poll_once() directly, no thread
    return w, sent


def test_fresh_task_done_before_first_poll_is_notified():
    w, sent = make({"op1": DONE})
    w.watch("op1", "my_export")
    w.poll_once()
    assert sent == [("✅ Task completed", "my_export\nDuration: 1h 2m 3s")]
    assert not w._pending


def test_failed_task_includes_error():
    w, sent = make({"op1": FAILED})
    w.watch("op1", "x")
    w.poll_once()
    assert sent[0][0] == "❌ Task failed"
    assert "Computation timed out." in sent[0][1]


def test_seeded_task_already_done_is_silent():
    w, sent = make({"op1": DONE})
    w.watch("op1", "old", announce=False)
    w.poll_once()
    assert sent == [] and not w._pending


def test_seeded_task_running_then_done_is_notified():
    ops = {"op1": RUNNING}
    w, sent = make(ops)
    w.watch("op1", "resumed", announce=False)
    w.poll_once()
    assert sent == [] and "op1" in w._pending
    ops["op1"] = DONE
    w.poll_once()
    assert len(sent) == 1


def test_repeated_fetch_errors_drop_task():
    def boom(name):
        raise RuntimeError("404")

    sent = []
    w = TaskWatcher(fetch=boom, notify=lambda t, m: sent.append(t))
    w._thread = object()
    w.watch("op1", "x")
    for _ in range(MAX_FAILS - 1):
        w.poll_once()
    assert "op1" in w._pending
    w.poll_once()
    assert not w._pending and sent == []


def test_disable_clears_and_ignores_new_tasks():
    w, _ = make({"op1": RUNNING})
    w.watch("op1", "x")
    w.configure(False, 60)
    assert not w._pending
    w.watch("op2", "y")
    assert not w._pending


def test_format_duration():
    assert format_duration("2026-01-01T10:00:00Z", "2026-01-01T10:00:00Z") == "0s"
    assert format_duration("2026-01-01T10:00:00Z", "2026-01-01T10:05:00Z") == "5m"
    assert format_duration(None, "garbage") == "unknown"
