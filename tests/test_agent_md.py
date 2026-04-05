"""Tests for agent .md file generation."""

import os
import sys
from claude_bridge.agent_md import generate_agent_md, write_agent_md, delete_agent_md


class TestGenerateAgentMd:
    def test_contains_frontmatter(self):
        content = generate_agent_md("backend--api", "backend", "/projects/api", "API dev")
        assert "---" in content
        assert "name: bridge--backend--api" in content
        assert "isolation: worktree" in content
        assert "memory: project" in content

    def test_contains_purpose(self):
        content = generate_agent_md("backend--api", "backend", "/projects/api", "REST endpoints")
        assert "REST endpoints" in content

    def test_stop_hook_in_project_settings(self, tmp_path):
        from claude_bridge.agent_md import install_stop_hook
        import json
        project_dir = str(tmp_path / "project")
        os.makedirs(project_dir)
        path = install_stop_hook(project_dir, "backend--api")
        with open(path) as f:
            settings = json.load(f)
        hook_cmd = settings["hooks"]["Stop"][0]["hooks"][0]["command"]
        assert "on-complete" in hook_cmd and "session-id" in hook_cmd
        assert "--session-id backend--api" in hook_cmd

    def test_contains_tools(self):
        content = generate_agent_md("backend--api", "backend", "/projects/api", "API dev")
        assert "tools: Read, Edit, Write, Bash, Grep, Glob" in content

    def test_uses_sys_executable_for_python_path(self):
        """generate_agent_md must use sys.executable, not a hardcoded python3."""
        content = generate_agent_md("backend--api", "backend", "/projects/api", "API dev")
        assert sys.executable in content

    def test_stop_hook_uses_sys_executable(self, tmp_path):
        """install_stop_hook must embed sys.executable in the hook command."""
        import json
        from claude_bridge.agent_md import install_stop_hook
        project_dir = str(tmp_path / "project")
        os.makedirs(project_dir)
        path = install_stop_hook(project_dir, "backend--api")
        with open(path) as f:
            settings = json.load(f)
        hook_cmd = settings["hooks"]["Stop"][0]["hooks"][0]["command"]
        # When bridge-cli is installed it uses bridge-cli path; otherwise sys.executable
        # Either way the raw "python3" literal must NOT appear
        assert "python3" not in hook_cmd or sys.executable in hook_cmd

    def test_stop_hook_contains_bridge_home(self, tmp_path, monkeypatch):
        """install_stop_hook must include CLAUDE_BRIDGE_HOME in hook command."""
        import json
        from claude_bridge.agent_md import install_stop_hook
        custom_home = str(tmp_path / "custom-bridge-home")
        monkeypatch.setenv("CLAUDE_BRIDGE_HOME", custom_home)
        project_dir = str(tmp_path / "project")
        os.makedirs(project_dir)
        path = install_stop_hook(project_dir, "backend--api")
        with open(path) as f:
            settings = json.load(f)
        hook_cmd = settings["hooks"]["Stop"][0]["hooks"][0]["command"]
        assert f"CLAUDE_BRIDGE_HOME={custom_home}" in hook_cmd

    def test_stop_hook_default_bridge_home(self, tmp_path, monkeypatch):
        """install_stop_hook is backward-compatible when CLAUDE_BRIDGE_HOME is not set."""
        import json
        from claude_bridge.agent_md import install_stop_hook
        monkeypatch.delenv("CLAUDE_BRIDGE_HOME", raising=False)
        project_dir = str(tmp_path / "project")
        os.makedirs(project_dir)
        path = install_stop_hook(project_dir, "backend--api")
        with open(path) as f:
            settings = json.load(f)
        hook_cmd = settings["hooks"]["Stop"][0]["hooks"][0]["command"]
        # Default home contains ".claude-bridge"
        assert "CLAUDE_BRIDGE_HOME=" in hook_cmd
        assert ".claude-bridge" in hook_cmd


class TestInstancePrefixInAgentMd:
    def test_generate_uses_instance_prefix(self, tmp_path, monkeypatch):
        """generate_agent_md embeds instance-prefixed name when home is non-default."""
        custom = tmp_path / ".claude-bridge-prod"
        custom.mkdir()
        monkeypatch.setenv("CLAUDE_BRIDGE_HOME", str(custom))
        content = generate_agent_md("backend--api", "backend", "/projects/api", "API dev")
        assert "name: bridge--prod--backend--api" in content

    def test_generate_no_prefix_for_default_home(self, monkeypatch):
        """generate_agent_md uses plain bridge-- prefix for default home."""
        monkeypatch.delenv("CLAUDE_BRIDGE_HOME", raising=False)
        content = generate_agent_md("backend--api", "backend", "/projects/api", "API dev")
        assert "name: bridge--backend--api" in content
        assert "bridge--prod" not in content

    def test_write_uses_instance_prefix_in_filename(self, tmp_path, monkeypatch):
        """write_agent_md writes file with instance-prefixed name."""
        custom = tmp_path / ".claude-bridge-prod"
        custom.mkdir()
        monkeypatch.setenv("CLAUDE_BRIDGE_HOME", str(custom))
        monkeypatch.setenv("HOME", str(tmp_path))
        content = generate_agent_md("backend--api", "backend", "/projects/api", "API dev")
        path = write_agent_md("backend--api", content)
        assert path.endswith("bridge--prod--backend--api.md")
        assert os.path.isfile(path)

    def test_delete_removes_instance_prefixed_file(self, tmp_path, monkeypatch):
        """delete_agent_md removes the correct instance-prefixed file."""
        custom = tmp_path / ".claude-bridge-prod"
        custom.mkdir()
        monkeypatch.setenv("CLAUDE_BRIDGE_HOME", str(custom))
        monkeypatch.setenv("HOME", str(tmp_path))
        content = generate_agent_md("backend--api", "backend", "/projects/api", "API dev")
        write_agent_md("backend--api", content)
        assert delete_agent_md("backend--api") is True
        # File should be gone
        agents_dir = os.path.join(str(tmp_path), ".claude", "agents")
        assert not os.path.isfile(os.path.join(agents_dir, "bridge--prod--backend--api.md"))

    def test_two_instances_different_files(self, tmp_path, monkeypatch):
        """Two instances create non-conflicting agent files."""
        prod = tmp_path / ".claude-bridge-prod"
        prod.mkdir()
        staging = tmp_path / ".claude-bridge-staging"
        staging.mkdir()

        monkeypatch.setenv("HOME", str(tmp_path))

        monkeypatch.setenv("CLAUDE_BRIDGE_HOME", str(prod))
        content_prod = generate_agent_md("backend--api", "backend", "/p/api", "API dev")
        path_prod = write_agent_md("backend--api", content_prod)

        monkeypatch.setenv("CLAUDE_BRIDGE_HOME", str(staging))
        content_staging = generate_agent_md("backend--api", "backend", "/p/api", "API dev")
        path_staging = write_agent_md("backend--api", content_staging)

        assert path_prod != path_staging
        assert os.path.isfile(path_prod)
        assert os.path.isfile(path_staging)
