from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from local_project_mcp.project_tools import get_git_diff, get_git_status, list_files, read_file
from local_project_mcp.security import ProjectAccessError, ProjectSecurityPolicy


TEXT_EXTENSIONS = {
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".css",
    ".dart",
    ".gradle",
    ".groovy",
    ".h",
    ".hpp",
    ".html",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".kt",
    ".kts",
    ".md",
    ".markdown",
    ".php",
    ".properties",
    ".proto",
    ".py",
    ".rs",
    ".sh",
    ".sql",
    ".swift",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
LOW_VALUE_FILENAMES = {
    ".gitignore",
    "gradlew",
    "gradlew.bat",
    "gradle-wrapper.properties",
}
SOURCE_EXTENSIONS = {
    ".java",
    ".kt",
    ".kts",
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".cs",
    ".cpp",
    ".cc",
    ".h",
    ".hpp",
    ".rs",
    ".swift",
    ".dart",
}
CLASS_EXTENSIONS = {".java", ".kt", ".kts", ".py", ".ts", ".tsx", ".js", ".jsx", ".cs", ".cpp", ".cc", ".h", ".hpp"}
CLASS_PATTERN = re.compile(
    r"\b(?:class|interface|object|enum\s+class|data\s+class|sealed\s+class|record)\s+([A-Za-z_][A-Za-z0-9_]*)"
)
DEPENDENCY_PATTERN = re.compile(
    r"\b(?:implementation|api|compileOnly|runtimeOnly|testImplementation|androidTestImplementation|ksp|classpath)\s*\([^)\n]+\)"
    r"|^\s*[A-Za-z0-9_.-]+\s*=\s*\"[^\"]+\"",
    re.MULTILINE,
)
STOP_WORDS = {
    "api",
    "component",
    "workspace",
    "агент",
    "ассистент",
    "все",
    "где",
    "для",
    "документацию",
    "изменения",
    "используется",
    "использования",
    "как",
    "код",
    "компонент",
    "найди",
    "обнови",
    "проект",
    "проверь",
    "создай",
    "файл",
}


@dataclass(frozen=True)
class WorkspaceConfig:
    enabled: bool = True
    reports_dir: str = "workspace_reports"
    allow_apply: bool = False
    max_files_read: int = 30
    max_write_bytes: int = 200_000


def workspace_run(
    project_path: str,
    goal: str,
    policy: ProjectSecurityPolicy,
    config: WorkspaceConfig,
    mode: str = "draft",
) -> dict[str, Any]:
    if not config.enabled:
        raise ProjectAccessError("workspace assistant is disabled by MCP config")
    clean_goal = goal.strip()
    if not clean_goal:
        raise ProjectAccessError("goal must not be empty")

    clean_mode = mode.strip().lower() or "draft"
    if clean_mode not in {"plan", "draft", "report", "apply"}:
        raise ProjectAccessError("workspace mode must be one of: plan, draft, report, apply")
    if clean_mode == "apply" and not config.allow_apply:
        raise ProjectAccessError("workspace apply mode is disabled by MCP config")
    if clean_mode == "apply":
        raise ProjectAccessError("workspace apply mode is not implemented yet")

    project = policy.validate_project_path(project_path)
    workflow = _classify_workflow(clean_goal)
    project_files = _project_file_list(str(project), policy)
    files = _select_files(str(project), clean_goal, workflow, policy, config.max_files_read)
    findings = _build_findings(str(project), clean_goal, workflow, files, policy)
    plan = _build_plan(workflow, clean_goal)
    answer = _answer(clean_goal, workflow, findings, files)

    result: dict[str, Any] = {
        "project_path": str(project),
        "goal": clean_goal,
        "mode": clean_mode,
        "workflow": workflow,
        "plan": plan,
        "files_read": [item["relative_path"] for item in files],
        "project_files": project_files[:300],
        "files_written": [],
        "findings": findings,
        "summary": _summary(workflow, findings, files),
        "answer": answer,
        "file_excerpts": _file_excerpts(files),
    }
    report = _format_report(result)
    result["report"] = report

    if clean_mode == "report":
        report_path = _write_report(project, config, workflow, clean_goal, report, policy)
        result["files_written"] = [report_path]
        result["artifact"] = report_path

    return result


def workspace_diff(project_path: str, policy: ProjectSecurityPolicy) -> dict[str, Any]:
    project = policy.validate_project_path(project_path)
    status = get_git_status(str(project), policy)
    diff = get_git_diff(str(project), policy)
    return {
        "project_path": str(project),
        "status": status.get("status", ""),
        "diff": diff.get("diff", ""),
        "truncated": diff.get("truncated", False),
    }


def workspace_apply_patch(
    project_path: str,
    patch: str,
    policy: ProjectSecurityPolicy,
    config: WorkspaceConfig,
    dry_run: bool = True,
) -> dict[str, Any]:
    if not config.enabled:
        raise ProjectAccessError("workspace assistant is disabled by MCP config")
    if not dry_run and not config.allow_apply:
        raise ProjectAccessError("workspace apply mode is disabled by MCP config")

    project = policy.validate_project_path(project_path)
    clean_patch = patch.strip()
    if not clean_patch:
        raise ProjectAccessError("patch must not be empty")
    if len(clean_patch.encode("utf-8")) > config.max_write_bytes:
        raise ProjectAccessError("patch is larger than workspace_max_write_bytes")

    touched_files = _parse_patch_paths(clean_patch)
    if not touched_files:
        raise ProjectAccessError("patch does not contain unified diff file headers")
    for relative_path in touched_files:
        policy.assert_write_allowed(project, relative_path)

    _run_git_apply(project, clean_patch, check_only=True)
    if not dry_run:
        _run_git_apply(project, clean_patch, check_only=False)

    return {
        "project_path": str(project),
        "dry_run": dry_run,
        "applied": not dry_run,
        "touched_files": touched_files,
    }


def _classify_workflow(goal: str) -> str:
    folded = goal.casefold()
    if any(term in folded for term in ("библиотек", "зависимост", "dependency", "dependencies", "libraries", "gradle")):
        return "dependency_analysis"
    if any(term in folded for term in ("сколько клас", "count class", "classes", "классов", "классы")):
        return "code_stats"
    if any(term in folded for term in ("changelog", "change log", "список изменений", "изменения")):
        return "changelog"
    if any(term in folded for term in ("инвариант", "invariant", "правил", "соответств")):
        return "invariant_check"
    if any(term in folded for term in ("diff", "дифф", "патч")):
        return "diff_summary"
    if any(term in folded for term in ("документац", "readme", "docs", "adr")):
        return "documentation_update"
    if any(term in folded for term in ("использ", "usage", "usages", "references", "где используется")):
        return "usage_analysis"
    return "project_analysis"


def _select_files(
    project_path: str,
    goal: str,
    workflow: str,
    policy: ProjectSecurityPolicy,
    max_files: int,
) -> list[dict[str, Any]]:
    paths = _project_file_list(project_path, policy)
    candidates = [path for path in paths if _looks_text_path(path) and not _is_low_value_path(path)]

    if workflow == "dependency_analysis":
        ordered = _ordered_general_context(project_path, candidates, goal, policy)
        ordered = sorted(ordered, key=lambda path: (0 if _is_dependency_path(path) else 1, path))
    elif workflow in {"changelog", "diff_summary"}:
        changed = _changed_files(project_path, policy)
        ordered = [path for path in changed if path in candidates]
        ordered.extend(path for path in _ordered_general_context(project_path, candidates, goal, policy) if path not in ordered)
    elif workflow == "documentation_update":
        ordered = _ordered_general_context(project_path, candidates, goal, policy)
        ordered = sorted(ordered, key=lambda path: (0 if _is_doc_path(path) else 1, path))
    elif workflow == "code_stats":
        ordered = _ordered_general_context(project_path, candidates, goal, policy)
        ordered = sorted(ordered, key=lambda path: (0 if Path(path).suffix.casefold() in CLASS_EXTENSIONS else 1, path))
    elif workflow == "invariant_check":
        ordered = _ordered_general_context(project_path, candidates, goal, policy)
        ordered = sorted(ordered, key=lambda path: (0 if "invariant" in path.casefold() else 1, path))
    else:
        ordered = _ordered_general_context(project_path, candidates, goal, policy)

    return _read_selected(project_path, ordered[: max(1, max_files)], policy)


def _project_file_list(project_path: str, policy: ProjectSecurityPolicy) -> list[str]:
    files = list_files(project_path, policy).get("files", [])
    return [path for path in files if isinstance(path, str)]


def _ordered_general_context(
    project_path: str,
    candidates: list[str],
    goal: str,
    policy: ProjectSecurityPolicy,
) -> list[str]:
    ranked = _rank_paths_by_goal(project_path, candidates, goal, policy)
    priority = sorted(
        candidates,
        key=lambda path: (
            _context_priority(path),
            path.count("/"),
            path,
        ),
    )
    ordered: list[str] = []
    for path in ranked + priority:
        if path not in ordered:
            ordered.append(path)
    return ordered


def _context_priority(path: str) -> int:
    folded = path.casefold()
    name = Path(path).name.casefold()
    if _is_doc_path(path):
        return 0
    if _is_dependency_path(path) or name in {"settings.gradle.kts", "settings.gradle", "gradle.properties"}:
        return 1
    if name in {"main.kt", "main.java", "main.py", "app.kt", "app.py", "agent.kt", "agent.py"}:
        return 2
    if Path(path).suffix.casefold() in SOURCE_EXTENSIONS:
        return 3
    if "src/" in folded:
        return 4
    return 9


def _rank_paths_by_goal(
    project_path: str,
    paths: list[str],
    goal: str,
    policy: ProjectSecurityPolicy,
) -> list[str]:
    terms = _goal_terms(goal)
    scored: list[tuple[int, str]] = []
    fallback: list[str] = []
    for path in paths:
        try:
            content = str(read_file(project_path, path, policy).get("content", ""))
        except ProjectAccessError:
            continue
        folded_path = path.casefold()
        folded_content = content.casefold()
        score = sum(folded_path.count(term) * 3 + folded_content.count(term) for term in terms)
        if score:
            scored.append((score, path))
        elif _is_doc_path(path):
            fallback.append(path)
    scored.sort(key=lambda item: (-item[0], item[1]))
    ranked = [path for _, path in scored]
    ranked.extend(path for path in fallback if path not in ranked)
    ranked.extend(path for path in paths if path not in ranked)
    return ranked


def _read_selected(project_path: str, paths: list[str], policy: ProjectSecurityPolicy) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    for path in paths:
        try:
            document = read_file(project_path, path, policy)
        except ProjectAccessError:
            continue
        content = str(document.get("content", ""))
        documents.append(
            {
                "relative_path": str(document.get("relative_path", path)),
                "content": content,
                "size_bytes": document.get("size_bytes", len(content.encode("utf-8"))),
            }
        )
    return documents


def _build_findings(
    project_path: str,
    goal: str,
    workflow: str,
    files: list[dict[str, Any]],
    policy: ProjectSecurityPolicy,
) -> list[dict[str, Any]]:
    if workflow == "usage_analysis":
        return _usage_findings(goal, files)
    if workflow in {"changelog", "diff_summary"}:
        diff = workspace_diff(project_path, policy)
        return [
            {"type": "git_status", "text": str(diff.get("status", "")).strip()},
            {"type": "git_diff", "text": _truncate(str(diff.get("diff", "")).strip(), 6000)},
        ]
    if workflow == "code_stats":
        return _code_stats_findings(files)
    if workflow == "dependency_analysis":
        return _dependency_findings(files)
    if workflow == "invariant_check":
        return _invariant_findings(files)
    if workflow == "documentation_update":
        return _documentation_findings(files)
    return _project_findings(files)


def _usage_findings(goal: str, files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    terms = _goal_terms(goal)
    findings: list[dict[str, Any]] = []
    for document in files:
        content = str(document["content"])
        for line_number, line in enumerate(content.splitlines(), start=1):
            folded = line.casefold()
            if any(term in folded for term in terms):
                findings.append(
                    {
                        "type": "usage",
                        "path": document["relative_path"],
                        "line": line_number,
                        "text": line.strip(),
                    }
                )
                if len(findings) >= 80:
                    return findings
    return findings


def _invariant_findings(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for document in files:
        content = str(document["content"])
        has_rule_text = any(term in content.casefold() for term in ("must", "should", "долж", "нельзя", "только"))
        findings.append(
            {
                "type": "file_check",
                "path": document["relative_path"],
                "text": "contains rule-like language" if has_rule_text else "no obvious rule-like language",
            }
        )
    return findings


def _code_stats_findings(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    class_count = 0
    file_count = 0
    for document in files:
        path = str(document["relative_path"])
        if Path(path).suffix.casefold() not in CLASS_EXTENSIONS:
            continue
        matches = CLASS_PATTERN.findall(str(document["content"]))
        if not matches:
            continue
        file_count += 1
        class_count += len(matches)
        findings.append(
            {
                "type": "code_classes",
                "path": path,
                "text": ", ".join(matches[:30]),
                "count": len(matches),
            }
        )
    return [{"type": "code_stats", "text": f"classes={class_count}, files_with_classes={file_count}"}] + findings


def _dependency_findings(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    count = 0
    for document in files:
        path = str(document["relative_path"])
        if not _is_dependency_path(path):
            continue
        declarations = [match.group(0).strip() for match in DEPENDENCY_PATTERN.finditer(str(document["content"]))]
        if not declarations:
            continue
        count += len(declarations)
        findings.append(
            {
                "type": "dependency_file",
                "path": path,
                "text": "; ".join(declarations[:30]),
                "count": len(declarations),
            }
        )
    return [{"type": "dependency_stats", "text": f"dependency_declarations={count}"}] + findings


def _documentation_findings(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for document in files:
        content = str(document["content"])
        heading_count = sum(1 for line in content.splitlines() if line.lstrip().startswith("#"))
        findings.append(
            {
                "type": "doc_snapshot",
                "path": document["relative_path"],
                "text": f"headings={heading_count}, size_bytes={document['size_bytes']}",
            }
        )
    return findings


def _project_findings(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "file_snapshot",
            "path": document["relative_path"],
            "text": _first_meaningful_line(str(document["content"])),
        }
        for document in files
    ]


def _build_plan(workflow: str, goal: str) -> list[str]:
    return [
        f"Classify goal as {workflow}.",
        "List allowed project files through MCP security policy.",
        "Read relevant text files selected from the goal.",
        "Analyze evidence and prepare a reproducible Markdown artifact.",
        "Write only a workspace report unless apply mode is explicitly enabled.",
    ]


def _summary(workflow: str, findings: list[dict[str, Any]], files: list[dict[str, Any]]) -> str:
    if workflow == "code_stats" and findings:
        return f"Workspace code_stats: {findings[0].get('text')}."
    if workflow == "dependency_analysis" and findings:
        return f"Workspace dependency_analysis: {findings[0].get('text')}."
    return f"Workspace {workflow}: read {len(files)} files and produced {len(findings)} findings."


def _answer(goal: str, workflow: str, findings: list[dict[str, Any]], files: list[dict[str, Any]]) -> str:
    if workflow == "usage_analysis":
        usages = [finding for finding in findings if finding.get("type") == "usage"]
        if not usages:
            return (
                "По проекту не нашел прямых использований по этому запросу. "
                f"Проверил релевантные текстовые файлы: {len(files)}."
            )
        lines = [f"Нашел {len(usages)} использований:"]
        for finding in usages[:12]:
            location = f"{finding.get('path')}:{finding.get('line')}"
            lines.append(f"- {location} - {finding.get('text')}")
        if len(usages) > 12:
            lines.append(f"...и еще {len(usages) - 12}.")
        return "\n".join(lines)

    if workflow == "code_stats":
        if not findings:
            return "Классы в прочитанных исходниках не найдены."
        stats = str(findings[0].get("text") or "")
        class_items = [finding for finding in findings[1:] if finding.get("type") == "code_classes"]
        lines = [f"Статистика классов: {stats}."]
        for finding in class_items[:12]:
            lines.append(f"- {finding.get('path')}: {finding.get('text')}")
        return "\n".join(lines)

    if workflow == "dependency_analysis":
        if not findings:
            return "Декларации библиотек и зависимостей в прочитанных файлах не найдены."
        stats = str(findings[0].get("text") or "")
        dependency_files = [finding for finding in findings[1:] if finding.get("type") == "dependency_file"]
        lines = [f"Зависимости проекта: {stats}."]
        for finding in dependency_files[:12]:
            lines.append(f"- {finding.get('path')}: {finding.get('text')}")
        return "\n".join(lines)

    if workflow in {"changelog", "diff_summary"}:
        status = next((str(item.get("text") or "") for item in findings if item.get("type") == "git_status"), "")
        diff = next((str(item.get("text") or "") for item in findings if item.get("type") == "git_diff"), "")
        if not status and not diff:
            return "Git-изменений не найдено."
        return "\n".join(part for part in ["Текущие изменения:", status, diff] if part)

    if workflow == "documentation_update":
        docs = [finding for finding in findings if finding.get("type") == "doc_snapshot"]
        if not docs:
            return "Документационные файлы по запросу не найдены."
        lines = [f"Проверил документацию: {len(docs)} файлов."]
        lines.extend(f"- {finding.get('path')}: {finding.get('text')}" for finding in docs[:12])
        return "\n".join(lines)

    if workflow == "invariant_check":
        flagged = [finding for finding in findings if "contains" in str(finding.get("text") or "")]
        return f"Проверил файлы на rule-like инварианты. Найдено файлов с правилами: {len(flagged)}."

    snapshots = [finding for finding in findings if finding.get("type") == "file_snapshot"]
    if not snapshots:
        return "По запросу не найдено достаточно данных в доступных файлах проекта."
    lines = [f"Проверил проект по запросу: {goal}"]
    lines.extend(f"- {finding.get('path')}: {finding.get('text')}" for finding in snapshots[:8])
    return "\n".join(lines)


def _format_report(result: dict[str, Any]) -> str:
    lines = [
        "# Workspace Report",
        "",
        f"Goal: {result['goal']}",
        f"Mode: {result['mode']}",
        f"Workflow: {result['workflow']}",
        "",
        "## Plan",
    ]
    lines.extend(f"- {step}" for step in result["plan"])
    lines.extend(["", "## Files Read"])
    lines.extend(f"- `{path}`" for path in result["files_read"] or ["none"])
    lines.extend(["", "## Findings"])
    for finding in result["findings"]:
        path = finding.get("path")
        line = finding.get("line")
        location = f" `{path}`" if path else ""
        if line:
            location += f":{line}"
        text = str(finding.get("text", "")).strip()
        lines.append(f"- {finding.get('type')}{location}: {text or 'empty'}")
    lines.extend(["", "## Summary", str(result["summary"]), ""])
    return "\n".join(lines)


def _file_excerpts(files: list[dict[str, Any]]) -> list[dict[str, str]]:
    excerpts: list[dict[str, str]] = []
    for document in files[:12]:
        content = str(document.get("content") or "")
        excerpts.append(
            {
                "relative_path": str(document.get("relative_path") or ""),
                "text": _truncate(content, 3500),
            }
        )
    return excerpts


def _write_report(
    project: Path,
    config: WorkspaceConfig,
    workflow: str,
    goal: str,
    report: str,
    policy: ProjectSecurityPolicy,
) -> str:
    encoded = report.encode("utf-8")
    if len(encoded) > config.max_write_bytes:
        raise ProjectAccessError("workspace report is larger than workspace_max_write_bytes")
    reports_dir = Path(config.reports_dir)
    if reports_dir.is_absolute() or any(part == ".." for part in reports_dir.parts):
        raise ProjectAccessError("workspace_reports_dir must be relative and must not contain '..'")
    target_dir = policy.resolve_relative_path(project, reports_dir)
    if policy.is_blocked_path(target_dir, project):
        raise ProjectAccessError("workspace_reports_dir points to a blocked path")
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{workflow}_{_slug(goal)}.md"
    target = policy.resolve_relative_path(project, reports_dir / filename)
    target.write_text(report, encoding="utf-8")
    return target.relative_to(project).as_posix()


def _parse_patch_paths(patch: str) -> list[str]:
    paths: list[str] = []
    for line in patch.splitlines():
        if not line.startswith("diff --git "):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        for raw_path in (parts[2], parts[3]):
            clean_path = raw_path.removeprefix("a/").removeprefix("b/")
            if clean_path == "/dev/null":
                continue
            if clean_path not in paths:
                paths.append(clean_path)
    return paths


def _run_git_apply(project: Path, patch: str, *, check_only: bool) -> None:
    args = ["git", "-C", str(project), "apply", "--whitespace=nowarn"]
    if check_only:
        args.append("--check")
    patch_input = patch if patch.endswith("\n") else patch + "\n"
    try:
        completed = subprocess.run(
            args,
            input=patch_input.encode("utf-8"),
            capture_output=True,
            timeout=10,
            check=False,
        )
    except FileNotFoundError as error:
        raise ProjectAccessError("git executable was not found") from error
    except subprocess.TimeoutExpired as error:
        raise ProjectAccessError("git apply timed out") from error

    if completed.returncode != 0:
        details = completed.stderr.decode("utf-8", errors="replace").strip()
        raise ProjectAccessError(f"git apply failed: {details or 'unknown error'}")


def _changed_files(project_path: str, policy: ProjectSecurityPolicy) -> list[str]:
    status = str(get_git_status(project_path, policy).get("status", ""))
    changed: list[str] = []
    for line in status.splitlines():
        if not line or line.startswith("##"):
            continue
        path = line[3:].strip() if len(line) > 3 else ""
        if " -> " in path:
            path = path.rsplit(" -> ", maxsplit=1)[-1]
        if path:
            changed.append(path)
    return changed


def _goal_terms(goal: str) -> list[str]:
    quoted = re.findall(r"[`'\"]([^`'\"]+)[`'\"]", goal)
    raw_terms = quoted or re.findall(r"[\w.:-]+", goal, flags=re.UNICODE)
    terms = []
    for term in raw_terms:
        clean = term.strip().casefold()
        if len(clean) < 3 or clean in STOP_WORDS:
            continue
        terms.append(clean)
    return terms or [goal.casefold()]


def _looks_text_path(path: str) -> bool:
    suffix = Path(path).suffix.casefold()
    return suffix in TEXT_EXTENSIONS or suffix == ""


def _is_low_value_path(path: str) -> bool:
    return Path(path).name.casefold() in LOW_VALUE_FILENAMES


def _is_doc_path(path: str) -> bool:
    folded = path.casefold()
    return folded.startswith("readme") or "/docs/" in f"/{folded}" or folded.startswith("docs/")


def _is_dependency_path(path: str) -> bool:
    folded = path.casefold()
    name = Path(path).name.casefold()
    return (
        "gradle" in folded
        or name in {"libs.versions.toml", "pyproject.toml", "package.json", "requirements.txt", "pom.xml"}
    )


def _first_meaningful_line(content: str) -> str:
    for line in content.splitlines():
        clean = line.strip()
        if clean:
            return _truncate(clean, 240)
    return ""


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "\n[truncated]"


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9а-яА-Я_-]+", "-", text.strip()).strip("-").lower()
    return (slug or "goal")[:48]
