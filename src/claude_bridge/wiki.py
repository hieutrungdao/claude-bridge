"""Wiki memory — compounding knowledge layer across agents.

Implements M23.T1: wiki home directory helper plus path-boundary-safe
read/write primitives. Later wiki tasks (ingest/query/lint) build on top of
these helpers and must never write outside `wiki_home()`.
"""

from __future__ import annotations

from datetime import date
from importlib import resources
from pathlib import Path

from . import get_bridge_home


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


def _load_schema_template() -> str:
    """Load the bundled schema template via importlib.resources."""
    return (
        resources.files("claude_bridge.prompts")
        .joinpath("wiki_schema_template.md")
        .read_text(encoding="utf-8")
    )
