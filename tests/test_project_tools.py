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
from local_project_mcp.support_rag import (
    clear_support_data,
    collect_project_overview,
    collect_project_code_stats,
    collect_answer_facts,
    plan_support_context,
    prepare_support_context,
    rag_build,
    rag_search,
    rag_status,
)


@pytest.fixture()
def policy(tmp_path: Path) -> ProjectSecurityPolicy:
    config = ServerConfig(
        allowed_roots=[str(tmp_path)],
        blocked_names=[".env", ".env.local", ".env.production", "id_rsa", "id_ed25519"],
        blocked_suffixes=[".local.yaml", ".local.yml", ".pem", ".key", ".p12", ".sqlite", ".db"],
        blocked_dirs=[".git", ".venv", "venv", "node_modules", "__pycache__", ".idea", ".vscode", ".gradle", "dist", "build", "target"],
        max_file_size_bytes=1000,
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


def test_read_docs_includes_code_files_when_scanning_project_root(project: Path, policy: ProjectSecurityPolicy) -> None:
    app = project / "app" / "src" / "main"
    app.mkdir(parents=True)
    (project / "build.gradle.kts").write_text("plugins { kotlin(\"android\") }", encoding="utf-8")
    (app / "MainActivity.kt").write_text("class MainActivity", encoding="utf-8")
    (app / "AndroidManifest.xml").write_text("<manifest />", encoding="utf-8")

    result = read_docs(str(project), policy, docs_paths=["."])

    paths = {document["relative_path"] for document in result["documents"]}
    assert "build.gradle.kts" in paths
    assert "app/src/main/MainActivity.kt" in paths
    assert "app/src/main/AndroidManifest.xml" in paths


def test_read_docs_includes_utf8_text_files_without_known_extension(project: Path, policy: ProjectSecurityPolicy) -> None:
    script = project / "gradlew"
    binary = project / "image"
    script.write_text("#!/bin/sh\n./gradle\n", encoding="utf-8")
    binary.write_bytes(b"\x00\x01\x02")

    result = read_docs(str(project), policy, docs_paths=["."])

    paths = {document["relative_path"] for document in result["documents"]}
    assert "gradlew" in paths
    assert "image" not in paths


def test_collect_project_code_stats_counts_classes(project: Path, policy: ProjectSecurityPolicy) -> None:
    src = project / "app" / "src" / "main" / "java" / "example"
    src.mkdir(parents=True)
    (src / "MainActivity.kt").write_text(
        "class MainActivity\ninterface WeatherApi\nenum class WeatherState { SUNNY }",
        encoding="utf-8",
    )

    stats = collect_project_code_stats(str(project), policy, docs_paths=["."])

    assert stats["class_count"] == 3
    assert {item["name"] for item in stats["classes"]} == {"MainActivity", "WeatherApi", "WeatherState"}


def test_support_context_planner_fallback_marks_count_questions() -> None:
    plan = plan_support_context("Сколько классов в проекте?")

    assert plan["source"] == "heuristic"
    assert plan["needs_code_stats"] is True


def test_collect_answer_facts_includes_code_stats(project: Path, policy: ProjectSecurityPolicy) -> None:
    src = project / "src"
    src.mkdir()
    (src / "Repository.kt").write_text("class Repository", encoding="utf-8")
    plan = plan_support_context("Сколько классов в проекте?")
    code_stats = collect_project_code_stats(str(project), policy, docs_paths=["."])

    facts = collect_answer_facts(str(project), "Сколько классов в проекте?", policy, ["."], plan, code_stats)

    assert facts[0]["type"] == "code_stats"
    assert facts[0]["data"]["class_count"] == 1


def test_project_overview_uses_readme_and_source_files(project: Path, policy: ProjectSecurityPolicy) -> None:
    src = project / "app" / "src" / "main" / "kotlin"
    src.mkdir(parents=True)
    (project / "README.md").write_text(
        "# Weather Station\n\nAndroid-приложение для просмотра измерений локальной погодной станции.",
        encoding="utf-8",
    )
    (src / "MainViewModel.kt").write_text("class MainViewModel", encoding="utf-8")

    overview = collect_project_overview(str(project), policy, docs_paths=["."])

    assert overview["summary"] == "Android-приложение для просмотра измерений локальной погодной станции."
    assert overview["source_files_count"] == 1
    assert overview["source_files_sample"] == ["app/src/main/kotlin/MainViewModel.kt"]


def test_prepare_support_context_does_not_require_ticket(project: Path, policy: ProjectSecurityPolicy) -> None:
    (project / "README.md").write_text(
        "# Weather Station\n\nAndroid-приложение для просмотра измерений локальной погодной станции.",
        encoding="utf-8",
    )

    result = prepare_support_context(
        str(project),
        "",
        "О чём вообще проект?",
        policy,
        docs_paths=["."],
        index_dir="rag",
        index_base_path=str(project),
    )

    assert result["ticket"] is None
    assert result["user"] is None
    assert result["answer_facts"][0]["type"] == "project_overview"


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


def test_rag_build_status_and_search(project: Path, tmp_path: Path, policy: ProjectSecurityPolicy) -> None:
    docs = project / "docs"
    docs.mkdir()
    (docs / "auth.md").write_text(
        "# Авторизация\n\nЕсли не работает авторизация, проверьте redirect_uri и OAuth client.",
        encoding="utf-8",
    )

    index_base_path = str(tmp_path / "mcp_support_data")
    build_result = rag_build(str(project), policy, docs_paths=["docs"], index_dir="rag", index_base_path=index_base_path)
    status = rag_status(str(project), policy, index_dir="rag", index_base_path=index_base_path)
    search_result = rag_search(
        str(project),
        "почему не работает авторизация",
        policy,
        index_dir="rag",
        index_base_path=index_base_path,
    )

    assert build_result["documents"] == 1
    assert str(project) not in str(build_result["index_file"])
    assert status["exists"] is True
    assert status["chunks"] == 1
    assert search_result["matches"][0]["relative_path"] == "docs/auth.md"


def test_rag_search_filters_low_score_noise(project: Path, tmp_path: Path, policy: ProjectSecurityPolicy) -> None:
    docs = project / "docs"
    docs.mkdir()
    (docs / "unrelated.md").write_text("alpha beta gamma delta", encoding="utf-8")
    index_base_path = str(tmp_path / "mcp_support_data")
    rag_build(str(project), policy, docs_paths=["docs"], index_dir="rag", index_base_path=index_base_path)

    result = rag_search(str(project), "измерения частота датчик", policy, index_dir="rag", index_base_path=index_base_path)

    assert result["matches"] == []


def test_prepare_support_context_returns_ticket_user_and_chunks(project: Path, policy: ProjectSecurityPolicy) -> None:
    docs = project / "docs"
    support = project / "support"
    docs.mkdir()
    support.mkdir()
    (docs / "auth.md").write_text(
        "# Авторизация\n\nОшибка invalid_oauth_redirect_uri означает неверный redirect URI.",
        encoding="utf-8",
    )
    (support / "tickets.json").write_text(
        '[{"id": "T-1", "user_id": "U-1", "status": "open", "last_error": "invalid_oauth_redirect_uri"}]',
        encoding="utf-8",
    )
    (support / "users.json").write_text(
        '[{"id": "U-1", "email": "user@example.test", "plan": "pro"}]',
        encoding="utf-8",
    )

    result = prepare_support_context(
        str(project),
        "T-1",
        "Почему не работает авторизация?",
        policy,
        docs_paths=["docs"],
        index_dir="rag",
        index_base_path=str(project),
    )

    assert result["ticket"]["last_error"] == "invalid_oauth_redirect_uri"
    assert result["user"]["plan"] == "pro"
    assert result["chunks"][0]["relative_path"] == "docs/auth.md"


def test_prepare_support_context_can_read_support_data_from_another_allowed_project(
    project: Path,
    tmp_path: Path,
    policy: ProjectSecurityPolicy,
) -> None:
    docs = project / "docs"
    docs.mkdir()
    (docs / "auth.md").write_text("# Auth\n\nAuthorization checks redirect URI.", encoding="utf-8")
    crm_project = tmp_path / "crm"
    support = crm_project / "support"
    support.mkdir(parents=True)
    (support / "tickets.json").write_text('[{"id": "T-2", "user_id": "U-2"}]', encoding="utf-8")
    (support / "users.json").write_text('[{"id": "U-2", "plan": "enterprise"}]', encoding="utf-8")

    result = prepare_support_context(
        str(project),
        "T-2",
        "Authorization problem",
        policy,
        support_data_path=str(crm_project),
        docs_paths=["docs"],
        index_dir="rag",
        index_base_path=str(crm_project),
    )

    assert result["support_data_path"] == str(crm_project)
    assert result["ticket"]["id"] == "T-2"
    assert result["user"]["plan"] == "enterprise"


def test_clear_support_data_clears_tickets_and_histories(tmp_path: Path, policy: ProjectSecurityPolicy) -> None:
    support_data = tmp_path / "support_data"
    support = support_data / "support"
    support.mkdir(parents=True)
    tickets = support / "tickets.json"
    histories = support / "histories.json"
    users = support / "users.json"
    tickets.write_text('[{"id": "T-1"}]', encoding="utf-8")
    histories.write_text('[{"ticket_id": "T-1", "messages": []}]', encoding="utf-8")
    users.write_text('[{"id": "U-1"}]', encoding="utf-8")

    result = clear_support_data(policy, str(support_data))

    assert result["cleared"] == ["support/tickets.json", "support/histories.json"]
    assert tickets.read_text(encoding="utf-8") == "[]\n"
    assert histories.read_text(encoding="utf-8") == "[]\n"
    assert users.read_text(encoding="utf-8") == '[{"id": "U-1"}]'


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
