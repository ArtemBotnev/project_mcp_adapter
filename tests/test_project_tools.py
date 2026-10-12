from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from local_project_mcp.config import ServerConfig
from local_project_mcp.project_tools import (
    get_git_branch,
    get_git_diff,
    list_docs,
    list_files,
    read_docs,
    read_file,
)
from local_project_mcp.security import ProjectAccessError, ProjectSecurityPolicy


@pytest.fixture()
def policy(tmp_path: Path) -> ProjectSecurityPolicy:
    config = ServerConfig(
        allowed_roots=[str(tmp_path)],
        blocked_names=[".env", ".env.local", ".env.production", "id_rsa", "id_ed25519"],
        blocked_suffixes=[".local.yaml", ".local.yml", ".pem", ".key", ".p12", ".sqlite", ".db"],
        blocked_dirs=[".git", ".venv", "venv", "node_modules", "__pycache__", ".idea", ".vscode", "dist", "build", "target"],
        max_file_size_bytes=100,
        max_list_files=5000,
        max_diff_bytes=200,
    )
    return ProjectSecurityPolicy.from_config(config)


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    path = tmp_path / "project"
    path.mkdir()
    return path


def test_project_path_outside_allowed_roots_is_denied(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    policy = ProjectSecurityPolicy.from_config(ServerConfig(allowed_roots=[str(allowed)]))

    with pytest.raises(ProjectAccessError, match="outside allowed_roots"):
        policy.validate_project_path(outside)


def test_relative_path_with_parent_segments_is_denied(project: Path, policy: ProjectSecurityPolicy) -> None:
    with pytest.raises(ProjectAccessError, match="must not contain"):
        policy.resolve_relative_path(project, "../README.md")


def test_symlink_escaping_project_is_denied(project: Path, tmp_path: Path, policy: ProjectSecurityPolicy) -> None:
    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("secret", encoding="utf-8")
    link = project / "outside-link.txt"
    link.symlink_to(outside_file)

    with pytest.raises(ProjectAccessError, match="outside project_path"):
        read_file(str(project), "outside-link.txt", policy)


def test_env_file_is_denied(project: Path, policy: ProjectSecurityPolicy) -> None:
    (project / ".env").write_text("TOKEN=secret", encoding="utf-8")

    with pytest.raises(ProjectAccessError, match="blocked path"):
        read_file(str(project), ".env", policy)


def test_local_yaml_suffix_is_denied(project: Path, policy: ProjectSecurityPolicy) -> None:
    config_dir = project / "agent_config"
    config_dir.mkdir()
    (config_dir / "llm.local.yaml").write_text("token: secret", encoding="utf-8")

    with pytest.raises(ProjectAccessError, match="blocked path"):
        read_file(str(project), "agent_config/llm.local.yaml", policy)


def test_git_dir_is_not_listed(project: Path, policy: ProjectSecurityPolicy) -> None:
    (project / ".git").mkdir()
    (project / ".git" / "config").write_text("[core]", encoding="utf-8")
    (project / "README.md").write_text("readme", encoding="utf-8")

    result = list_files(str(project), policy)

    assert "README.md" in result["files"]
    assert ".git/config" not in result["files"]


def test_get_git_branch_works_in_temporary_repo(project: Path, policy: ProjectSecurityPolicy) -> None:
    _git(project, "init")
    _git(project, "checkout", "-b", "task-31")

    result = get_git_branch(str(project), policy)

    assert result["branch"] == "task-31"


def test_read_file_reads_regular_readme(project: Path, policy: ProjectSecurityPolicy) -> None:
    (project / "README.md").write_text("hello", encoding="utf-8")

    result = read_file(str(project), "README.md", policy)

    assert result["relative_path"] == "README.md"
    assert result["content"] == "hello"
    assert result["size_bytes"] == 5


def test_read_docs_returns_readme_and_docs_markdown(project: Path, policy: ProjectSecurityPolicy) -> None:
    docs = project / "docs"
    docs.mkdir()
    (project / "README.md").write_text("readme", encoding="utf-8")
    (docs / "architecture.md").write_text("arch", encoding="utf-8")
    (docs / "image.png").write_text("not docs", encoding="utf-8")

    result = read_docs(str(project), policy)

    paths = {document["relative_path"] for document in result["documents"]}
    assert paths == {"README.md", "docs/architecture.md"}
    assert result["skipped"] == []


def test_get_git_diff_truncates_large_diff(project: Path, policy: ProjectSecurityPolicy) -> None:
    _git(project, "init")
    _git(project, "config", "user.email", "test@example.com")
    _git(project, "config", "user.name", "Test User")
    readme = project / "README.md"
    readme.write_text("initial\n", encoding="utf-8")
    _git(project, "add", "README.md")
    _git(project, "commit", "-m", "initial")
    readme.write_text("initial\n" + ("changed line\n" * 100), encoding="utf-8")

    result = get_git_diff(str(project), policy)

    assert result["truncated"] is True
    assert "[output truncated to 200 bytes]" in result["diff"]
    assert len(str(result["diff"]).encode("utf-8")) > 200


def _git(cwd: Path, *args: str) -> None:
    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "Test User",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test User",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        }
    )
    subprocess.run(["git", *args], cwd=cwd, check=True, env=env, capture_output=True)

