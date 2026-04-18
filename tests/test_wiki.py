"""Tests for M23.T1 — Wiki home + schema seed.

Covers wiki_home(), safe_write(), safe_read(), and the seeded schema/index/log
bundled at src/claude_bridge/prompts/wiki_schema_template.md.

These tests fail until src/claude_bridge/wiki.py exists. That's intentional (TDD).
"""

from __future__ import annotations

import builtins
import os
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# wiki_home() — lazy init, idempotency, env-var override
# ---------------------------------------------------------------------------


class TestWikiHome:
    def test_returns_path_under_bridge_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_BRIDGE_HOME", str(tmp_path))
        from claude_bridge.wiki import wiki_home

        result = wiki_home()
        assert isinstance(result, Path)
        assert result == tmp_path / "wiki"

    def test_first_call_creates_directory(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_BRIDGE_HOME", str(tmp_path))
        from claude_bridge.wiki import wiki_home

        assert not (tmp_path / "wiki").exists()
        wiki_home()
        assert (tmp_path / "wiki").is_dir()

    def test_first_call_seeds_schema_index_log(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_BRIDGE_HOME", str(tmp_path))
        from claude_bridge.wiki import wiki_home

        home = wiki_home()
        assert (home / "schema.md").is_file()
        assert (home / "index.md").is_file()
        assert (home / "log.md").is_file()

    def test_schema_contains_expected_sections(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_BRIDGE_HOME", str(tmp_path))
        from claude_bridge.wiki import wiki_home

        schema = (wiki_home() / "schema.md").read_text()
        # Attribution
        assert "Karpathy" in schema
        # Operations
        for op in ("Ingest", "Query", "Lint"):
            assert op in schema, f"schema.md missing operation: {op}"
        # Template fields
        for section in ("Summary", "Key Facts", "Cross-references", "Open Questions"):
            assert section in schema, f"schema.md missing template section: {section}"
        # Cross-ref convention
        assert "[[" in schema and "]]" in schema
        # Naming convention hint
        assert "kebab-case" in schema or "kebab case" in schema.lower()

    def test_index_has_last_updated_iso_date(self, tmp_path, monkeypatch):
        import re

        monkeypatch.setenv("CLAUDE_BRIDGE_HOME", str(tmp_path))
        from claude_bridge.wiki import wiki_home

        index = (wiki_home() / "index.md").read_text()
        assert "# Wiki Index" in index
        assert re.search(r"Last updated:\s*\d{4}-\d{2}-\d{2}", index)

    def test_log_is_header_only_initially(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_BRIDGE_HOME", str(tmp_path))
        from claude_bridge.wiki import wiki_home

        log = (wiki_home() / "log.md").read_text()
        assert "# Wiki Log" in log
        # No ingest/query/lint entries yet
        assert "| INGEST |" not in log
        assert "| QUERY |" not in log
        assert "| LINT |" not in log


# ---------------------------------------------------------------------------
# Idempotency — never overwrite user edits
# ---------------------------------------------------------------------------


class TestIdempotentInit:
    def test_second_call_does_not_rewrite_schema(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_BRIDGE_HOME", str(tmp_path))
        from claude_bridge.wiki import wiki_home

        home = wiki_home()
        custom = "# My Custom Schema\n\nUser edited this.\n"
        (home / "schema.md").write_text(custom)

        wiki_home()  # re-init
        assert (home / "schema.md").read_text() == custom

    def test_second_call_does_not_rewrite_index(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_BRIDGE_HOME", str(tmp_path))
        from claude_bridge.wiki import wiki_home

        home = wiki_home()
        custom = "# Custom index\n"
        (home / "index.md").write_text(custom)

        wiki_home()
        assert (home / "index.md").read_text() == custom

    def test_second_call_does_not_rewrite_log(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_BRIDGE_HOME", str(tmp_path))
        from claude_bridge.wiki import wiki_home

        home = wiki_home()
        with_entry = (home / "log.md").read_text() + "\n2026-04-19 | INGEST | test\n"
        (home / "log.md").write_text(with_entry)

        wiki_home()
        assert (home / "log.md").read_text() == with_entry


# ---------------------------------------------------------------------------
# safe_write() — happy path, overwrite, nested dirs
# ---------------------------------------------------------------------------


class TestSafeWriteHappyPath:
    def test_writes_to_wiki_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_BRIDGE_HOME", str(tmp_path))
        from claude_bridge.wiki import safe_write, wiki_home

        path = safe_write("foo.md", "hello")
        assert path == (wiki_home() / "foo.md").resolve()
        assert path.read_text() == "hello"

    def test_creates_parent_directories(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_BRIDGE_HOME", str(tmp_path))
        from claude_bridge.wiki import safe_write, wiki_home

        path = safe_write("entities/api/patterns.md", "x")
        assert path.read_text() == "x"
        assert (wiki_home() / "entities" / "api").is_dir()

    def test_overwrite_false_rejects_existing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_BRIDGE_HOME", str(tmp_path))
        from claude_bridge.wiki import safe_write

        safe_write("foo.md", "first")
        with pytest.raises(FileExistsError):
            safe_write("foo.md", "second")

    def test_overwrite_true_replaces(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_BRIDGE_HOME", str(tmp_path))
        from claude_bridge.wiki import safe_write

        safe_write("foo.md", "first")
        path = safe_write("foo.md", "second", overwrite=True)
        assert path.read_text() == "second"


# ---------------------------------------------------------------------------
# safe_read() — happy path
# ---------------------------------------------------------------------------


class TestSafeRead:
    def test_reads_seeded_schema(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_BRIDGE_HOME", str(tmp_path))
        from claude_bridge.wiki import safe_read, wiki_home

        wiki_home()  # trigger seed
        content = safe_read("schema.md")
        assert "Karpathy" in content

    def test_read_missing_raises_filenotfound(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_BRIDGE_HOME", str(tmp_path))
        from claude_bridge.wiki import safe_read, wiki_home

        wiki_home()
        with pytest.raises(FileNotFoundError):
            safe_read("nonexistent.md")


# ---------------------------------------------------------------------------
# Path-boundary guard — the load-bearing security property
# ---------------------------------------------------------------------------


class TestBoundaryGuard:
    def test_parent_traversal_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_BRIDGE_HOME", str(tmp_path))
        from claude_bridge.wiki import safe_write

        with pytest.raises(ValueError):
            safe_write("../outside.md", "x")

    def test_deep_traversal_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_BRIDGE_HOME", str(tmp_path))
        from claude_bridge.wiki import safe_write

        with pytest.raises(ValueError):
            safe_write("../../../../etc/passwd", "x")

    def test_absolute_path_outside_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_BRIDGE_HOME", str(tmp_path))
        from claude_bridge.wiki import safe_write

        escape = tmp_path.parent / "escape.md"
        with pytest.raises(ValueError):
            safe_write(str(escape), "x")

    def test_safe_read_rejects_traversal(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_BRIDGE_HOME", str(tmp_path))
        from claude_bridge.wiki import safe_read, wiki_home

        wiki_home()
        with pytest.raises(ValueError):
            safe_read("../../../etc/passwd")

    def test_symlink_escape_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_BRIDGE_HOME", str(tmp_path))
        from claude_bridge.wiki import safe_write, wiki_home

        home = wiki_home()
        outside = tmp_path.parent / "outside_target"
        outside.mkdir(exist_ok=True)
        link = home / "escape"
        link.symlink_to(outside)

        # Writing through the symlink must be rejected because the
        # resolved path is outside wiki home.
        with pytest.raises(ValueError):
            safe_write("escape/pwned.md", "x")


# ---------------------------------------------------------------------------
# The ownership-boundary invariant: never write to Claude Code's memory
# ---------------------------------------------------------------------------


class TestNeverWritesToClaudeMemory:
    def test_init_does_not_touch_claude_projects(self, tmp_path, monkeypatch):
        """wiki_home() init must not write anywhere under ~/.claude/projects/."""
        monkeypatch.setenv("CLAUDE_BRIDGE_HOME", str(tmp_path))

        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()
        (fake_home / ".claude" / "projects").mkdir(parents=True)
        monkeypatch.setenv("HOME", str(fake_home))

        opened_for_write: list[str] = []
        real_open = builtins.open

        def tracking_open(file, mode="r", *args, **kwargs):
            if isinstance(file, (str, os.PathLike)) and any(
                m in mode for m in ("w", "a", "x", "+")
            ):
                opened_for_write.append(os.fspath(file))
            return real_open(file, mode, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", tracking_open)

        from claude_bridge.wiki import wiki_home

        wiki_home()

        forbidden_prefix = str(fake_home / ".claude" / "projects")
        violations = [p for p in opened_for_write if p.startswith(forbidden_prefix)]
        assert not violations, f"wiki_home() wrote to forbidden path(s): {violations}"

    def test_safe_write_does_not_touch_claude_projects(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_BRIDGE_HOME", str(tmp_path))
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()
        (fake_home / ".claude" / "projects").mkdir(parents=True)
        monkeypatch.setenv("HOME", str(fake_home))

        opened_for_write: list[str] = []
        real_open = builtins.open

        def tracking_open(file, mode="r", *args, **kwargs):
            if isinstance(file, (str, os.PathLike)) and any(
                m in mode for m in ("w", "a", "x", "+")
            ):
                opened_for_write.append(os.fspath(file))
            return real_open(file, mode, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", tracking_open)

        from claude_bridge.wiki import safe_write

        safe_write("entities/foo.md", "x")

        forbidden_prefix = str(fake_home / ".claude" / "projects")
        violations = [p for p in opened_for_write if p.startswith(forbidden_prefix)]
        assert not violations, f"safe_write wrote to forbidden path(s): {violations}"


# ---------------------------------------------------------------------------
# CLAUDE_BRIDGE_HOME override is honored end-to-end
# ---------------------------------------------------------------------------


class TestCustomBridgeHome:
    def test_custom_home_redirects_wiki(self, tmp_path, monkeypatch):
        custom = tmp_path / "custom-bridge"
        monkeypatch.setenv("CLAUDE_BRIDGE_HOME", str(custom))
        from claude_bridge.wiki import wiki_home

        home = wiki_home()
        assert home == custom / "wiki"
        assert (home / "schema.md").is_file()
