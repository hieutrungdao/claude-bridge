"""Tests for watcher.py — fallback PID checker."""

import json
from unittest.mock import patch
from datetime import datetime, timedelta, timezone

import pytest

from claude_bridge.db import BridgeDB
from claude_bridge.message_db import MessageDB
from claude_bridge.watcher import watch


@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "test.db")
    database = BridgeDB(db_path)
    yield database
    database.close()


@pytest.fixture
def agent_with_running_task(db, tmp_path):
    """Create an agent with a running task."""
    db.create_agent("backend", "/p/api", "backend--api", "/a.md", "dev")
    tid = db.create_task("backend--api", "fix bug")
    result_file = str(tmp_path / f"task-{tid}-result.json")
    db.update_task(
        tid,
        status="running",
        pid=99999,
        result_file=result_file,
        started_at=datetime.now(timezone.utc).isoformat(),
    )
    db.update_agent_state("backend--api", "running")
    return {"task_id": tid, "result_file": result_file, "session_id": "backend--api"}


class TestWatcherDeadPid:
    @patch("claude_bridge.watcher.pid_alive", return_value=False)
    def test_dead_pid_with_result_marks_done(self, mock_alive, db, agent_with_running_task):
        """Dead PID + successful result → task done, agent idle."""
        info = agent_with_running_task
        with open(info["result_file"], "w") as f:
            json.dump({"is_error": False, "result": "done", "total_cost_usd": 0.03, "duration_ms": 60000, "num_turns": 3}, f)

        watch.__wrapped__(db) if hasattr(watch, '__wrapped__') else _run_watch(db)

        task = db.get_task(info["task_id"])
        assert task["status"] == "done"

        agent = db.get_agent("backend")
        assert agent["state"] == "idle"  # Agent MUST be idle, not "failed"

    @patch("claude_bridge.watcher.pid_alive", return_value=False)
    def test_dead_pid_no_result_marks_failed(self, mock_alive, db, agent_with_running_task):
        """Dead PID + no result file → task failed, agent idle."""
        info = agent_with_running_task
        # Don't write result file

        _run_watch(db)

        task = db.get_task(info["task_id"])
        assert task["status"] == "failed"

        agent = db.get_agent("backend")
        assert agent["state"] == "idle"  # Agent MUST be idle, not "failed"


class TestWatcherTimeout:
    @patch("claude_bridge.watcher.kill_process")
    @patch("claude_bridge.watcher.pid_alive", return_value=True)
    def test_timeout_kills_and_marks(self, mock_alive, mock_kill, db, tmp_path):
        """Running > timeout → kill + mark timeout, agent idle."""
        db.create_agent("backend", "/p/api", "backend--api", "/a.md", "dev")
        tid = db.create_task("backend--api", "long task")
        # Set started_at to 40 minutes ago
        started = (datetime.now(timezone.utc) - timedelta(minutes=40)).isoformat()
        db.update_task(tid, status="running", pid=88888, started_at=started)
        db.update_agent_state("backend--api", "running")

        _run_watch(db, timeout_minutes=30)

        task = db.get_task(tid)
        assert task["status"] == "timeout"

        agent = db.get_agent("backend")
        assert agent["state"] == "idle"  # Agent MUST be idle, not "timeout"

        mock_kill.assert_called_once_with(88888)


class TestWatcherDequeue:
    @patch("claude_bridge.watcher.spawn_task", return_value=12345)
    @patch("claude_bridge.watcher.pid_alive", return_value=False)
    def test_dequeues_next_task_after_hook_missed(self, mock_alive, mock_spawn, db, agent_with_running_task, tmp_path):
        """Dead PID (hook missed) + queued task → queued task dequeued to pending."""
        info = agent_with_running_task
        with open(info["result_file"], "w") as f:
            json.dump({"is_error": False, "result": "done", "total_cost_usd": 0.01}, f)

        # Queue a second task
        queued_id = db.create_task("backend--api", "second task")
        db.update_task(queued_id, status="queued", position=1)

        _run_watch(db)

        queued_task = db.get_task(queued_id)
        assert queued_task["status"] == "running"
        mock_spawn.assert_called_once()


class TestWatcherNoOp:
    def test_no_running_tasks(self, db):
        """No running tasks → exits silently."""
        db.create_agent("backend", "/p/api", "backend--api", "/a.md", "dev")
        _run_watch(db)  # Should not raise


class TestWatcherReporting:
    @patch("claude_bridge.watcher.pid_alive", return_value=False)
    def test_reports_unreported_tasks(self, mock_alive, db, agent_with_running_task, capsys):
        info = agent_with_running_task
        with open(info["result_file"], "w") as f:
            json.dump({"is_error": False, "result": "fixed it", "total_cost_usd": 0.01}, f)

        _run_watch(db)

        captured = capsys.readouterr()
        assert "Task #" in captured.out

        # Task should be marked as reported
        unreported = db.get_unreported_tasks()
        assert len(unreported) == 0


def _run_watch(db: BridgeDB, timeout_minutes: int = 30):
    """Run the watch function with an injected db."""
    watch(timeout_minutes=timeout_minutes, db=db)


class TestWatcherInstanceScoping:
    def test_get_running_tasks_only_returns_tasks_with_known_agents(self, db):
        """get_running_tasks() joins with agents — only tasks with registered agents are returned."""
        db.create_agent("backend", "/p/api", "backend--api", "/a.md", "dev")
        tid = db.create_task("backend--api", "work")
        db.update_task(tid, status="running", pid=11111,
                       started_at=datetime.now(timezone.utc).isoformat())
        db.update_agent_state("backend--api", "running")

        running = db.get_running_tasks()
        assert len(running) == 1
        assert running[0]["session_id"] == "backend--api"

    def test_get_running_tasks_returns_empty_when_no_agents(self, db):
        """get_running_tasks() on empty agents table returns nothing."""
        # No agents registered → nothing to return even if tasks existed
        running = db.get_running_tasks()
        assert running == []

    @patch("claude_bridge.watcher.pid_alive", return_value=True)
    def test_watcher_processes_only_registered_agent_tasks(self, mock_alive, db):
        """Watcher processes tasks whose agents are registered in the current instance DB."""
        db.create_agent("backend", "/p/api", "backend--api", "/a.md", "dev")
        tid = db.create_task("backend--api", "do work")
        db.update_task(tid, status="running", pid=22222,
                       started_at=datetime.now(timezone.utc).isoformat())
        db.update_agent_state("backend--api", "running")

        _run_watch(db, timeout_minutes=9999)  # Large timeout — no kill

        # pid_alive was checked for the registered agent's task
        mock_alive.assert_called_once_with(22222)


class TestWatcherNoDuplicateOutbound:
    @patch("claude_bridge.watcher.pid_alive", return_value=False)
    def test_no_duplicate_outbound_on_second_watcher_run(self, mock_alive, db, tmp_path):
        """Running watcher twice on the same unreported task must create only one outbound."""
        db.create_agent("backend", "/p/api", "backend--api", "/a.md", "dev")
        tid = db.create_task("backend--api", "fix bug", channel="telegram", channel_chat_id="999")
        result_file = str(tmp_path / f"task-{tid}-result.json")
        with open(result_file, "w") as f:
            json.dump({"is_error": False, "result": "done", "total_cost_usd": 0.01,
                       "duration_ms": 5000, "num_turns": 2}, f)
        db.update_task(tid, status="running", pid=12345, result_file=result_file,
                       started_at=datetime.now(timezone.utc).isoformat())
        db.update_agent_state("backend--api", "running")

        msg_db_path = str(tmp_path / "messages.db")

        # First run: task completes
        with patch("claude_bridge.watcher.MessageDB", side_effect=lambda: MessageDB(msg_db_path)):
            _run_watch(db)

        task = db.get_task(tid)
        assert task["reported"] == 1

        db1 = MessageDB(msg_db_path)
        try:
            first_count = len(db1.get_pending_outbound())
            assert first_count == 1  # Exactly one outbound created
        finally:
            db1.close()

        # Reset reported flag to simulate watcher re-running before mark_task_reported
        db.conn.execute("UPDATE tasks SET reported = 0 WHERE id = ?", (tid,))
        db.conn.commit()

        # Second run — outbound already exists so should NOT create another
        with patch("claude_bridge.watcher.MessageDB", side_effect=lambda: MessageDB(msg_db_path)):
            _run_watch(db)

        db2 = MessageDB(msg_db_path)
        try:
            assert len(db2.get_pending_outbound()) == first_count
        finally:
            db2.close()
