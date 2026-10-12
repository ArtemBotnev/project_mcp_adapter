from __future__ import annotations

import fnmatch
import os
import subprocess
from pathlib import Path

from local_project_mcp.security import ProjectAccessError, ProjectSecurityPolicy


GIT_TIMEOUT_SECONDS = 10
DEFAULT_DOCS_PATHS = [
    "README.md",
    "README.*",
    "docs",
    "project/docs",
    "api",
    "openapi.yaml",
    "openapi.yml",
    "swagger.yaml",
    "swagger.yml",
]
DOC_EXTENSIONS = {".md", ".markdown", ".txt", ".yaml", ".yml", ".json", ".toml", ".proto"}


def get_git_branch(project_path: str, policy: ProjectSecurityPolicy) -> dict[str, str]:
    project = policy.validate_project_path(project_path)
    branch = _run_git(project, ["branch", "--show-current"]).strip()
    if not branch:
        branch = _run_git(project, ["rev-parse", "--abbrev-ref", "HEAD"]).strip()
    return {"project_path": str(project), "branch": branch}


def get_git_status(project_path: str, policy: ProjectSecurityPolicy) -> dict[str, str]:
    project = policy.validate_project_path(project_path)
    status, truncated = _run_git_limited(project, ["status", "--short", "--branch"], policy.max_diff_bytes)
    if truncated:
        status = _append_truncated_notice(status, policy.max_diff_bytes)
    return {"project_path": str(project), "status": status}


def get_git_diff(project_path: str, policy: ProjectSecurityPolicy, staged: bool = False) -> dict[str, object]:
    project = policy.validate_project_path(project_path)
    args = ["diff", "--cached", "--"] if staged else ["diff", "--"]
    diff, truncated = _run_git_limited(project, args, policy.max_diff_bytes)
    if truncated:
        diff = _append_truncated_notice(diff, policy.max_diff_bytes)
    return {"project_path": str(project), "staged": staged, "diff": diff, "truncated": truncated}


def list_files(project_path: str, policy: ProjectSecurityPolicy, subdir: str = ".") -> dict[str, object]:
    project = policy.validate_project_path(project_path)
    start = policy.resolve_relative_path(project, subdir)
    if not start.exists():
        raise ProjectAccessError(f"subdir does not exist: {subdir}")
    if not start.is_dir():
        raise ProjectAccessError(f"subdir is not a directory: {subdir}")
    if policy.is_blocked_path(start, project):
        raise ProjectAccessError(f"access to blocked path is denied: {subdir}")

    files: list[str] = []
    truncated = False
    for root, dirnames, filenames in os.walk(start, topdown=True, followlinks=False):
        root_path = Path(root)
        dirnames[:] = [
            dirname
            for dirname in sorted(dirnames)
            if _is_allowed_walk_dir(policy, project, root_path / dirname)
        ]
        for filename in sorted(filenames):
            candidate = root_path / filename
            try:
                allowed = policy.assert_read_allowed(project, candidate)
            except ProjectAccessError:
                continue
            files.append(allowed.relative_to(project).as_posix())
            if len(files) >= policy.max_list_files:
                truncated = True
                break
        if truncated:
            break

    return {"project_path": str(project), "subdir": subdir, "files": files, "truncated": truncated}


def read_file(project_path: str, relative_path: str, policy: ProjectSecurityPolicy) -> dict[str, object]:
    project = policy.validate_project_path(project_path)
    path = policy.resolve_relative_path(project, relative_path)
    path = policy.assert_read_allowed(project, path)
    size = path.stat().st_size
    if size > policy.max_file_size_bytes:
        raise ProjectAccessError("file is larger than max_file_size_bytes")
    return {
        "project_path": str(project),
        "relative_path": path.relative_to(project).as_posix(),
        "content": path.read_text(encoding="utf-8", errors="replace"),
        "size_bytes": size,
    }


def list_docs(
    project_path: str,
    policy: ProjectSecurityPolicy,
    docs_paths: list[str] | None = None,
) -> dict[str, object]:
    project = policy.validate_project_path(project_path)
    paths = docs_paths or DEFAULT_DOCS_PATHS
    docs: set[str] = set()

    for docs_path in paths:
        if not docs_path:
            continue
        if _has_path_traversal(docs_path):
            raise ProjectAccessError("docs_paths entries must be relative and must not contain '..'")
        if _has_glob(docs_path):
            for candidate in project.glob(docs_path):
                _collect_doc_candidate(policy, project, candidate, docs)
            continue

        candidate = policy.resolve_relative_path(project, docs_path)
        _collect_doc_candidate(policy, project, candidate, docs)

    return {"project_path": str(project), "docs": sorted(docs)}


def read_docs(
    project_path: str,
    policy: ProjectSecurityPolicy,
    docs_paths: list[str] | None = None,
) -> dict[str, object]:
    project = policy.validate_project_path(project_path)
    docs_result = list_docs(str(project), policy, docs_paths)
    documents: list[dict[str, object]] = []
    skipped: list[dict[str, str]] = []

    for relative_path in docs_result["docs"]:
        try:
            documents.append(read_file(str(project), str(relative_path), policy))
        except ProjectAccessError as error:
            skipped.append({"relative_path": str(relative_path), "reason": str(error)})

    return {"project_path": str(project), "documents": documents, "skipped": skipped}


def search_docs(
    project_path: str,
    query: str,
    policy: ProjectSecurityPolicy,
    docs_paths: list[str] | None = None,
    limit: int = 10,
) -> dict[str, object]:
    terms = [term for term in query.casefold().split() if term]
    if not terms:
        raise ProjectAccessError("query must not be empty")

    docs = read_docs(project_path, policy, docs_paths)
    matches: list[dict[str, object]] = []
    for document in docs["documents"]:
        content = str(document["content"])
        folded = content.casefold()
        counts = [folded.count(term) for term in terms]
        score = sum(counts)
        if score == 0:
            continue
        first = min(folded.find(term) for term in terms if folded.find(term) >= 0)
        matches.append(
            {
                "relative_path": document["relative_path"],
                "score": score,
                "snippet": _snippet(content, first),
            }
        )

    max_matches = max(1, min(limit, 100))
    matches.sort(key=lambda item: (-int(item["score"]), str(item["relative_path"])))
    return {"project_path": docs["project_path"], "query": query, "matches": matches[:max_matches]}


def _run_git(project: Path, args: list[str]) -> str:
    output, _ = _run_git_limited(project, args, None)
    return output


def _run_git_limited(project: Path, args: list[str], max_bytes: int | None) -> tuple[str, bool]:
    try:
        completed = subprocess.run(
            ["git", "-C", str(project), *args],
            capture_output=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as error:
        raise ProjectAccessError("git executable was not found") from error
    except subprocess.TimeoutExpired as error:
        raise ProjectAccessError("git command timed out") from error

    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise ProjectAccessError(stderr or f"git command failed with exit code {completed.returncode}")

    stdout = completed.stdout
    truncated = False
    if max_bytes is not None and len(stdout) > max_bytes:
        stdout = stdout[:max_bytes]
        truncated = True
    return stdout.decode("utf-8", errors="replace"), truncated


def _append_truncated_notice(text: str, max_bytes: int) -> str:
    suffix = f"\n[output truncated to {max_bytes} bytes]"
    return text.rstrip() + suffix


def _is_allowed_walk_dir(policy: ProjectSecurityPolicy, project: Path, path: Path) -> bool:
    try:
        resolved = path.resolve()
        if not resolved.is_dir() or not resolved.relative_to(project):
            return False
    except (OSError, ValueError):
        return False
    return not policy.is_blocked_path(path, project)


def _collect_doc_candidate(
    policy: ProjectSecurityPolicy,
    project: Path,
    candidate: Path,
    docs: set[str],
) -> None:
    if not candidate.exists():
        return
    if candidate.is_dir():
        try:
            listed = list_files(str(project), policy, candidate.relative_to(project).as_posix())
        except ProjectAccessError:
            return
        for relative in listed["files"]:
            if Path(str(relative)).suffix.lower() in DOC_EXTENSIONS:
                docs.add(str(relative))
        return
    if candidate.suffix.lower() not in DOC_EXTENSIONS:
        return
    try:
        allowed = policy.assert_read_allowed(project, candidate)
    except ProjectAccessError:
        return
    docs.add(allowed.relative_to(project).as_posix())


def _has_path_traversal(path: str) -> bool:
    parsed = Path(path)
    return parsed.is_absolute() or any(part == ".." for part in parsed.parts)


def _has_glob(path: str) -> bool:
    return any(char in path for char in "*?[")


def _snippet(content: str, index: int, radius: int = 120) -> str:
    start = max(0, index - radius)
    end = min(len(content), index + radius)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(content) else ""
    return prefix + content[start:end].replace("\n", " ").strip() + suffix
