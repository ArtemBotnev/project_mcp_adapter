from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from local_project_mcp.config import ServerConfig


class ProjectAccessError(Exception):
    """Raised when requested project access violates the configured policy."""


@dataclass(frozen=True)
class ProjectSecurityPolicy:
    allowed_roots: tuple[Path, ...]
    blocked_names: frozenset[str]
    blocked_suffixes: tuple[str, ...]
    blocked_dirs: frozenset[str]
    max_file_size_bytes: int
    max_list_files: int
    max_diff_bytes: int

    @classmethod
    def from_config(cls, config: ServerConfig) -> "ProjectSecurityPolicy":
        allowed_roots = tuple(Path(root).expanduser().resolve() for root in config.allowed_roots)
        return cls(
            allowed_roots=allowed_roots,
            blocked_names=frozenset(config.blocked_names),
            blocked_suffixes=tuple(config.blocked_suffixes),
            blocked_dirs=frozenset(config.blocked_dirs),
            max_file_size_bytes=config.max_file_size_bytes,
            max_list_files=config.max_list_files,
            max_diff_bytes=config.max_diff_bytes,
        )

    def validate_project_path(self, path: str | Path) -> Path:
        project_path = Path(path).expanduser().resolve()
        if not project_path.exists():
            raise ProjectAccessError(f"project_path does not exist: {project_path}")
        if not project_path.is_dir():
            raise ProjectAccessError(f"project_path is not a directory: {project_path}")
        if not self._is_within_allowed_roots(project_path):
            raise ProjectAccessError("project_path is outside allowed_roots")
        return project_path

    def resolve_relative_path(self, project_path: str | Path, relative_path: str | Path) -> Path:
        project = self.validate_project_path(project_path)
        relative = Path(relative_path)
        if relative.is_absolute():
            raise ProjectAccessError("relative_path must be relative")
        if any(part == ".." for part in relative.parts):
            raise ProjectAccessError("relative_path must not contain '..'")

        resolved = (project / relative).resolve()
        if not _is_relative_to(resolved, project):
            raise ProjectAccessError("path resolves outside project_path")
        if not self._is_within_allowed_roots(resolved):
            raise ProjectAccessError("path resolves outside allowed_roots")
        return resolved

    def is_blocked_path(self, path: str | Path, project_path: str | Path | None = None) -> bool:
        candidate = Path(path)
        parts = candidate.parts
        if project_path is not None:
            project = Path(project_path).expanduser().resolve()
            resolved = candidate.expanduser().resolve()
            if _is_relative_to(resolved, project):
                parts = resolved.relative_to(project).parts

        return self._has_blocked_parts(parts)

    def assert_read_allowed(self, project_path: str | Path, path: str | Path) -> Path:
        project = self.validate_project_path(project_path)
        requested = Path(path)
        resolved = requested.expanduser().resolve()
        if not _is_relative_to(resolved, project):
            raise ProjectAccessError("file resolves outside project_path")
        if not self._is_within_allowed_roots(resolved):
            raise ProjectAccessError("file resolves outside allowed_roots")

        self._assert_not_blocked(requested, project)
        self._assert_not_blocked(resolved, project)

        if not resolved.exists():
            raise ProjectAccessError(f"file does not exist: {resolved}")
        if not resolved.is_file():
            raise ProjectAccessError(f"path is not a file: {resolved}")
        return resolved

    def _assert_not_blocked(self, path: Path, project: Path) -> None:
        try:
            parts = path.resolve().relative_to(project).parts
        except ValueError:
            try:
                parts = path.relative_to(project).parts
            except ValueError:
                parts = path.parts
        if self._has_blocked_parts(parts):
            raise ProjectAccessError(f"access to blocked path is denied: {_safe_join(parts)}")

    def _has_blocked_parts(self, parts: tuple[str, ...]) -> bool:
        for part in parts:
            if part in self.blocked_dirs:
                return True
        name = parts[-1] if parts else ""
        return name in self.blocked_names or any(name.endswith(suffix) for suffix in self.blocked_suffixes)

    def _is_within_allowed_roots(self, path: Path) -> bool:
        return any(_is_relative_to(path, root) for root in self.allowed_roots)


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _safe_join(parts: tuple[str, ...]) -> str:
    return "/".join(parts) if parts else "."

