"""Wiki memory — compounding knowledge layer across agents.

Implements M23.T1: wiki home directory helper plus path-boundary-safe
read/write primitives. Later wiki tasks (ingest/query/lint) build on top of
these helpers and must never write outside `wiki_home()`.
"""

from __future__ import annotations

import os
from datetime import date
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

from . import get_bridge_home
from . import memory

if TYPE_CHECKING:
    from .db import BridgeDB


class SourceRecord(TypedDict):
    """One agent's Auto Memory snapshot — input to wiki ingest."""

    agent: str
    project_dir: str
    session_id: str
    found: bool
    memory_dir: str | None
    main_memory: str
    topics: list[dict]
    source_mtime: float | None


_INDEX_TEMPLATE = """\
# Wiki Index

> Content catalog for the Bridge Wiki. Last updated: {today}
> See `schema.md` for operating instructions and `log.md` for history.

---

## Entity Pages

| Page | Description |
|------|-------------|

_No entity pages yet. Run `bridge-cli wiki ingest` to synthesize from agent memories._
"""

_LOG_TEMPLATE = """\
# Wiki Log

> Append-only chronological record of all Ingest, Query, and Lint operations.
> Format: `YYYY-MM-DD | OPERATION | details`

---
"""


def wiki_home() -> Path:
    """Return the wiki home directory, initializing it on first access.

    Creates `<bridge_home>/wiki/` and seeds `schema.md`, `index.md`, and an
    empty `log.md` if they do not already exist. Idempotent — existing files
    are never overwritten.
    """
    home = get_bridge_home() / "wiki"
    home.mkdir(parents=True, exist_ok=True)

    schema_path = home / "schema.md"
    if not schema_path.exists():
        schema_path.write_text(_load_schema_template(), encoding="utf-8")

    index_path = home / "index.md"
    if not index_path.exists():
        index_path.write_text(
            _INDEX_TEMPLATE.format(today=date.today().isoformat()),
            encoding="utf-8",
        )

    log_path = home / "log.md"
    if not log_path.exists():
        log_path.write_text(_LOG_TEMPLATE, encoding="utf-8")

    return home


def safe_write(relative_path: str, content: str, *, overwrite: bool = False) -> Path:
    """Write content to a file inside the wiki home.

    Raises ValueError if `relative_path` resolves outside `wiki_home()`
    (after symlink resolution). Raises FileExistsError if the target exists
    and `overwrite` is False. Creates parent directories as needed.
    """
    resolved = _assert_inside_wiki(relative_path, must_exist=False)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    mode = "w" if overwrite else "x"
    with open(resolved, mode, encoding="utf-8") as f:
        f.write(content)
    return resolved


def safe_read(relative_path: str) -> str:
    """Read a file inside the wiki home. Raises ValueError if outside."""
    resolved = _assert_inside_wiki(relative_path, must_exist=True)
    with open(resolved, "r", encoding="utf-8") as f:
        return f.read()


def _assert_inside_wiki(relative_path: str, *, must_exist: bool) -> Path:
    """Resolve `relative_path` against the wiki home and assert it stays inside.

    Uses `Path.resolve()` so symlinks pointing outside the wiki are rejected.
    Absolute paths are permitted only if they resolve under the wiki home.
    """
    wiki = wiki_home().resolve()
    candidate = Path(relative_path)
    raw = candidate if candidate.is_absolute() else wiki / candidate
    resolved = raw.resolve()
    try:
        resolved.relative_to(wiki)
    except ValueError as exc:
        raise ValueError(
            f"Path {relative_path!r} resolves to {resolved} which is outside "
            f"wiki home {wiki}"
        ) from exc
    if must_exist and not resolved.is_file():
        raise FileNotFoundError(f"No such wiki file: {relative_path!r}")
    return resolved


def _load_template(name: str) -> str:
    """Load a bundled prompt template by filename via importlib.resources."""
    return (
        resources.files("claude_bridge.prompts")
        .joinpath(name)
        .read_text(encoding="utf-8")
    )


def _load_schema_template() -> str:
    """Load the seed schema template (kept as a named helper for M23.T1)."""
    return _load_template("wiki_schema_template.md")


def _load_ingest_template() -> str:
    """Load the static portion of the ingest prompt (consumed by M24.T2)."""
    return _load_template("wiki_ingest.md")


def collect_sources(
    agent_filter: str | None = None,
    db: "BridgeDB | None" = None,
) -> list[SourceRecord]:
    """Collect Auto Memory sources across agents for wiki ingest.

    Iterates every registered agent (optionally narrowed by `agent_filter`)
    and reads its Auto Memory via `memory.read_memory`. Computes a
    `source_mtime` as the max mtime of `*.md` files under the memory
    directory; staleness checking in later ingest uses this to skip
    unchanged agents.

    When `db` is None, a `BridgeDB()` is created and closed inside the
    function. An injected `db` is used as-is and never closed.

    Never writes under `~/.claude/projects/*` — the Auto Memory layer is
    strictly read-only from Bridge's perspective.
    """
    from .db import BridgeDB

    owned_db = db is None
    if db is None:
        db = BridgeDB()

    try:
        rows = db.list_agents()
    finally:
        if owned_db:
            db.close()

    records: list[SourceRecord] = []
    for row in rows:
        name = row["name"]
        if agent_filter is not None and name != agent_filter:
            continue
        project_dir = row["project_dir"]
        session_id = row["session_id"]
        mem = memory.read_memory(project_dir)
        records.append(
            {
                "agent": name,
                "project_dir": project_dir,
                "session_id": session_id,
                "found": bool(mem.get("found")),
                "memory_dir": mem.get("memory_dir"),
                "main_memory": mem.get("main", "") or "",
                "topics": mem.get("topics", []) or [],
                "source_mtime": _max_mtime(mem.get("memory_dir")),
            }
        )
    return records


def _max_mtime(memory_dir: str | None) -> float | None:
    """Return the newest mtime across `*.md` files in memory_dir, else None."""
    if not memory_dir:
        return None
    if not os.path.isdir(memory_dir):
        return None
    best: float | None = None
    for entry in os.listdir(memory_dir):
        if not entry.endswith(".md"):
            continue
        path = os.path.join(memory_dir, entry)
        if not os.path.isfile(path):
            continue
        mtime = os.path.getmtime(path)
        best = mtime if best is None else max(best, mtime)
    return best
