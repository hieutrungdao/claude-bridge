# Claude Bridge

Multi-session Claude Code dispatch from Telegram. Each session = agent + project.

## Architecture

Bridge Bot (Claude Code + Telegram MCP) → bridge-cli.py → spawns `claude --agent --session-id --worktree -p "task"` → Stop hook fires on-complete.py → SQLite updated → Telegram notified.

Built on top of native Claude Code features: `--agent`, `--session-id`, `isolation: worktree`, Auto Memory, Stop hooks, prompt caching.

## Project Structure

```
src/claude_bridge/       Python package (the core)
  cli.py                 CLI entry point (bridge-cli command dispatcher)
  db.py                  SQLite database module (agents + tasks)
  session.py             Session model (agent + project → session_id)
  agent_md.py            Native Claude Code agent .md file generator
  claude_md_init.py      Purpose-driven CLAUDE.md initialization
  dispatcher.py          Task spawner (subprocess.Popen + PID tracking)
  memory.py              Auto Memory reader
  on_complete.py         Stop hook handler (called by Claude Code)
  watcher.py             Fallback PID watcher (cron)
tests/                   pytest tests
plan/                    Architecture docs + implementation tasks
specs/                   Technical specifications
research/                Research from architecture exploration
```

## Key Concepts

- **Session = Agent + Project**: `backend` + `/projects/my-api` → session_id `backend--my-api`
- **Agent .md files**: Generated in `{bot_dir}/.claude/agents/bridge--{session_id}.md` (project-level, per-instance isolated)
- **Stop hook**: Agent frontmatter includes Stop hook → calls on-complete.py → updates SQLite
- **Worktree isolation**: Each task runs in isolated git worktree (no concurrent corruption)
- **Auto Memory**: Claude Code auto-learns patterns. Bridge reads via `/memory` command.

## Multi-Instance Setup

Claude Bridge supports multiple isolated instances using `CLAUDE_BRIDGE_HOME`:

**Main instance:**
```bash
bridge start              # Uses ~/.claude-bridge (default)
bridge stop
bridge status
```

**Additional instances (e.g., tam):**
```bash
# Setup once
CLAUDE_BRIDGE_HOME=~/.claude-bridge-tam \
  bridge-cli setup --token "<token>" --chat-id "<chat-id>" --bot-dir ~/projects/bridge-bot-tam --no-prompt

# Start/stop
CLAUDE_BRIDGE_HOME=~/.claude-bridge-tam bridge start    # Auto-uses unique session: claude-bridge-{hash}
CLAUDE_BRIDGE_HOME=~/.claude-bridge-tam bridge stop
CLAUDE_BRIDGE_HOME=~/.claude-bridge-tam bridge status
```

**Session names:**
- Default home (`~/.claude-bridge`) → session `claude-bridge`
- Other homes → session `claude-bridge-{md5hash[:8]}`
- Hash ensures no conflicts when running multiple instances

**Aliases for convenience:**
```bash
alias bridge-tam='CLAUDE_BRIDGE_HOME=~/.claude-bridge-tam bridge'
# Then: bridge-tam start, bridge-tam stop, etc.
```

## Build & Test

```bash
# Install in dev mode
pip install -e .

# Run tests
pytest

# Run CLI directly
python -m claude_bridge.cli create-agent backend /path/to/project --purpose "API dev"
python -m claude_bridge.cli dispatch backend "add pagination"
python -m claude_bridge.cli list-agents
python -m claude_bridge.cli status
```

## Dependencies

Python 3.11+ with stdlib only for the core package (sqlite3, subprocess, argparse, json, os, signal).
One optional dependency: `mcp>=1.0` — required only when using MCP server mode (`mcp_server.py`).
`claude` CLI must be installed and in PATH.

Note: `pyproject.toml` declares `mcp>=1.0` as a dependency so `pip install claude-agent-bridge`
works out-of-the-box for MCP mode. The core package (cli.py, db.py, session.py, dispatcher.py)
remains stdlib-only. Version constraint is `"mcp>=1.0,<2.0"` to avoid breaking changes.

## Conventions

- Pure Python, stdlib only — no external dependencies
- Single responsibility per module
- All state in SQLite (`~/.claude-bridge/bridge.db`)
- Agent .md files in native Claude Code format (YAML frontmatter + markdown)
- Error messages go to stderr, output goes to stdout
- Exit code 0 = success, non-zero = error

## Development & Deploy Flow

### Code → Build → Install → Setup → Restart → Test

Claude-bridge có 2 layers: Python (core logic) + TypeScript (MCP channel server).
Khi thay đổi code, phải follow đúng flow:

```bash
# 1. Sửa code
#    - Python: src/claude_bridge/*.py
#    - TypeScript: channel/server.ts (MCP tools exposed to Claude Code)

# 2. Build TypeScript (nếu sửa channel/server.ts)
cd channel && bun build server.ts --outdir dist && cd ..

# 3. Run tests
pytest tests/ --ignore=tests/test_telegram_poller.py

# 4. Install from source (editable mode)
pip install -e . --break-system-packages

# 5. Re-setup bot dirs (copies channel dist + updates CLAUDE.md + .mcp.json)
bridge-cli setup-bot ~/projects/bridge-bot                                    # main instance
CLAUDE_BRIDGE_HOME=~/.claude-bridge-tam bridge-cli setup-bot ~/projects/bridge-bot-tam  # tam instance

# 6. Restart instances
#    Main: systemctl --user restart claude-bridge
#    Tam:  CLAUDE_BRIDGE_HOME=~/.claude-bridge-tam bridge-cli daemon stop && bridge-cli daemon start

# 7. Test trên Telegram trước khi push

# 8. Commit, tag, push, release (khi đã test OK)
#    git commit → git tag v0.X.Y → git push --tags → gh release create v0.X.Y
#    GitHub Actions auto-publish to PyPI
```

QUAN TRỌNG:
- MCP tools phải có trong CẢ HAI: Python mcp_server.py VÀ TypeScript channel/server.ts
- Python mcp_server.py = logic implementation
- TypeScript channel/server.ts = tool exposed cho Claude Code (gọi bridge-cli CLI)
- Nếu chỉ thêm vào Python mà quên TypeScript → Claude Code không thấy tool
- setup-bot copy channel/dist/server.js vào CLAUDE_BRIDGE_HOME/channel/ — KHÔNG copy manual

### Multi-instance
- Main: CLAUDE_BRIDGE_HOME=~/.claude-bridge (default)
- Tam: CLAUDE_BRIDGE_HOME=~/.claude-bridge-tam
- Mỗi instance có DB, agents, config, channel riêng
- on_complete hook có CLAUDE_BRIDGE_HOME prefix

### TDD Process

Implementation follows a strict TDD process defined in `.claude/rules/phase-plan-approach.md`.
This process is reusable across all phases (1, 2, 3).

**Per task:** task spec → write tests → fix code → code review → commit
**Per milestone:** run full suite → write milestone report

Key rules:
- Task spec created BEFORE writing any code (specs/tasks/M{M}-T{T}.md)
- Tests written BEFORE fixing code (TDD)
- Code review checklist applied after each task (`.claude/rules/code-review.md`)
- Milestone report after each milestone (specs/reports/milestone-{N}-report.md)
- Commit format: `M{M}.T{T}: {description}`
- Never call real `claude` CLI in tests — always mock subprocess

## Release Process

Khi release version mới:
1. Bump version trong pyproject.toml
2. Update CHANGELOG.md
3. Commit changes
4. Tag version: git tag vX.Y.Z
5. Push commit + tag: git push && git push --tags
6. Tạo GitHub Release: gh release create vX.Y.Z --title "vX.Y.Z" --notes "..."
   QUAN TRỌNG: Phải tạo GitHub Release vì PyPI publish workflow trigger trên "release published" event, KHÔNG phải tag push.

## Debugging Critical Bugs

When asked to fix a critical bug, DO NOT jump to conclusions. Follow this process:

1. **Reproduce first** — confirm the exact failure. What input, what expected, what actual?
2. **Challenge your first theory** — your first explanation is probably wrong or incomplete. Argue against it. Ask: "what else could cause this?"
3. **Check the environment, not just the code** — zombie processes, stale state, competing services, wrong python version, missing files. Most "code bugs" are environment bugs.
4. **Don't blame external systems too early** — "it's a Claude Code bug" or "it's a Telegram API issue" is lazy. Prove it by ruling out your own code first.
5. **Add observability before guessing** — add logging/stderr output at each step so you can see WHERE it fails, not guess.
6. **Test the actual integration, not just units** — mocked tests passing means your logic is correct, NOT that the system works. Test with real transports, real processes, real files.
7. **Look for the boring cause** — competing processes, wrong file paths, stale caches, permission issues. The exciting theory (protocol corruption, race conditions) is usually wrong. The boring theory (zombie process stealing messages) is usually right.
