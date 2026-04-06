#!/usr/bin/env python3
"""Fallback PID watcher — catches tasks where the Stop hook didn't fire.

Run via cron: * * * * * PYTHONPATH=/path/to/claude-bridge/src python3 -m claude_bridge.watcher
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

from .db import BridgeDB
from .dispatcher import pid_alive, kill_process, spawn_task, get_result_file
from .message_db import MessageDB
from .session import derive_agent_file_name


DEFAULT_TIMEOUT_MINUTES = 360


def _repair_incomplete_done_tasks(db: BridgeDB) -> None:
    """Re-parse result files for done tasks that have no summary/cost data.

    Handles the race condition where the stop hook fires before the result
    JSON file is fully written. The watcher runs later and can recover the data.
    """
    from .on_complete import parse_result_file
    from .notify import format_completion_message

    incomplete = db.conn.execute(
        """SELECT t.*, a.name as agent_name
           FROM tasks t JOIN agents a ON t.session_id = a.session_id
           WHERE t.status = 'done'
             AND t.result_summary IS NULL
             AND t.result_file IS NOT NULL
             AND t.reported = 1"""
    ).fetchall()

    if not incomplete:
        return

    msg_db = MessageDB()
    try:
        for task in incomplete:
            result = parse_result_file(task["result_file"])
            if not result or result.get("is_error"):
                continue

            summary = str(result.get("result", ""))[:500]
            cost = result.get("total_cost_usd", 0) or 0
            duration = result.get("duration_ms", 0) or 0
            turns = result.get("num_turns", 0) or 0

            if not summary and cost == 0:
                continue  # Still no data — skip

            db.update_task(
                task["id"],
                result_summary=summary if summary else None,
                cost_usd=cost,
                duration_ms=duration,
                num_turns=turns,
            )
            print(f"[watcher] Repaired task #{task['id']} ({task['session_id']}) — recovered result data from file")

            # Send a follow-up notification with the real data
            if task["channel"] != "cli" and task["channel_chat_id"]:
                if not msg_db.has_notification_for_task(task["id"]):
                    updated = db.get_task(task["id"])
                    agent_name = task["agent_name"]
                    message = format_completion_message(updated, agent_name)
                    msg_db.create_outbound(
                        task["channel"], task["channel_chat_id"],
                        message, source="notification", task_id=task["id"],
                    )
    finally:
        msg_db.close()


def watch(timeout_minutes: int = DEFAULT_TIMEOUT_MINUTES, db: BridgeDB | None = None):
    """Check running tasks and handle completions/timeouts."""
    own_db = db is None
    if own_db:
        db = BridgeDB()

    try:
        running_tasks = db.get_running_tasks()

        for task in running_tasks:
            task_id = task["id"]
            pid = task["pid"]
            session_id = task["session_id"]
            started_at = task["started_at"]

            if not pid:
                # No PID recorded — mark as failed
                db.update_task(
                    task_id,
                    status="failed",
                    error_message="No PID recorded",
                    completed_at=datetime.now(timezone.utc).isoformat(),
                )
                db.update_agent_state(session_id, "idle")
                continue

            if not pid_alive(pid):
                # Process is dead but hook didn't fire — parse result if available
                from .on_complete import parse_result_file

                result_file = task["result_file"]
                result = parse_result_file(result_file) if result_file else None

                if result and not result.get("is_error"):
                    db.update_task(
                        task_id,
                        status="done",
                        result_summary=str(result.get("result", ""))[:500],
                        cost_usd=result.get("total_cost_usd", 0),
                        duration_ms=result.get("duration_ms", 0),
                        num_turns=result.get("num_turns", 0),
                        exit_code=0,
                        completed_at=datetime.now(timezone.utc).isoformat(),
                    )
                    db.increment_agent_tasks(session_id)
                    print(f"[watcher] Task #{task_id} ({session_id}) completed (hook missed)")
                else:
                    error = str(result.get("result", "Process exited"))[:500] if result else "Process exited unexpectedly"
                    db.update_task(
                        task_id,
                        status="failed",
                        error_message=error,
                        exit_code=-1,
                        completed_at=datetime.now(timezone.utc).isoformat(),
                    )
                    db.increment_agent_tasks(session_id)
                    print(f"[watcher] Task #{task_id} ({session_id}) failed (hook missed)")

                next_task = db.dequeue_next_task(session_id)
                if next_task:
                    agent = db.get_agent_by_session(session_id)
                    next_task_id = next_task["id"]
                    next_result_file = get_result_file(session_id, next_task_id)
                    pid = spawn_task(
                        derive_agent_file_name(session_id), session_id,
                        agent["project_dir"], next_task["prompt"], next_task_id,
                    )
                    db.update_task(
                        next_task_id,
                        status="running", pid=pid,
                        result_file=next_result_file,
                        started_at=datetime.now(timezone.utc).isoformat(),
                    )
                else:
                    db.update_agent_state(session_id, "idle")

            elif started_at:
                # Check timeout
                started = datetime.fromisoformat(started_at)
                # Handle naive timestamps from before UTC fix
                if started.tzinfo is None:
                    started = started.replace(tzinfo=timezone.utc)
                elapsed = (datetime.now(timezone.utc) - started).total_seconds()
                if elapsed > timeout_minutes * 60:
                    kill_process(pid)
                    db.update_task(
                        task_id,
                        status="timeout",
                        error_message=f"Timed out after {timeout_minutes} minutes",
                        completed_at=datetime.now(timezone.utc).isoformat(),
                    )
                    db.update_agent_state(session_id, "idle")
                    print(f"[watcher] Task #{task_id} ({session_id}) timed out after {timeout_minutes}m")

        # Re-parse done tasks that were reported with missing result data (race condition recovery)
        _repair_incomplete_done_tasks(db)

        # Report unreported completions + queue notifications
        from .notify import format_completion_message

        msg_db = MessageDB()
        try:
            unreported = db.get_unreported_tasks()
            for task in unreported:
                if task["status"] == "done":
                    print(f"✓ Task #{task['id']} ({task['session_id']}) — done")
                    if task["result_summary"]:
                        print(f"  {task['result_summary'][:200]}")
                elif task["status"] == "failed":
                    print(f"✗ Task #{task['id']} ({task['session_id']}) — failed")
                    if task["error_message"]:
                        print(f"  {task['error_message'][:200]}")
                elif task["status"] == "timeout":
                    print(f"⏱ Task #{task['id']} ({task['session_id']}) — timed out")

                # Queue notification via outbound messages (dedup: skip if already queued)
                if task["channel"] != "cli" and task["channel_chat_id"]:
                    if not msg_db.has_notification_for_task(task["id"]):
                        agent = db.get_agent_by_session(task["session_id"])
                        agent_name = agent["name"] if agent else task["session_id"]
                        message = format_completion_message(task, agent_name)
                        msg_db.create_outbound(
                            task["channel"], task["channel_chat_id"],
                            message, source="notification", task_id=task["id"],
                        )

                db.mark_task_reported(task["id"])
        finally:
            msg_db.close()

    finally:
        if own_db:
            db.close()


def main():
    watch()


if __name__ == "__main__":
    main()
