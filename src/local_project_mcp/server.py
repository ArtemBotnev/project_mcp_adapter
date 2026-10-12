from __future__ import annotations

import argparse
import os
from collections.abc import Callable
from typing import Any

try:
    from mcp.server.mcpserver import MCPServer as MCPApp
except ImportError:  # pragma: no cover - compatibility with mcp<2.
    from mcp.server.fastmcp import FastMCP as MCPApp

from local_project_mcp.config import load_config
from local_project_mcp.project_tools import (
    get_git_branch as tool_get_git_branch,
    get_git_diff as tool_get_git_diff,
    get_git_status as tool_get_git_status,
    list_docs as tool_list_docs,
    list_files as tool_list_files,
    read_docs as tool_read_docs,
    read_file as tool_read_file,
    search_docs as tool_search_docs,
)
from local_project_mcp.security import ProjectAccessError, ProjectSecurityPolicy


CONFIG_ENV = "LOCAL_PROJECT_MCP_CONFIG"


def build_mcp(config_path: str | None = None) -> MCPApp:
    config_file = config_path or os.environ.get(CONFIG_ENV, "config.yaml")
    policy = ProjectSecurityPolicy.from_config(load_config(config_file))
    mcp = MCPApp("local-project-mcp")

    @mcp.tool()
    def get_git_branch(project_path: str) -> dict[str, Any]:
        """Return the current git branch for a local project."""
        return _guard(lambda: tool_get_git_branch(project_path, policy))

    @mcp.tool()
    def get_git_status(project_path: str) -> dict[str, Any]:
        """Return read-only git status for a local project."""
        return _guard(lambda: tool_get_git_status(project_path, policy))

    @mcp.tool()
    def get_git_diff(project_path: str, staged: bool = False) -> dict[str, Any]:
        """Return git diff for a local project."""
        return _guard(lambda: tool_get_git_diff(project_path, policy, staged))

    @mcp.tool()
    def list_files(project_path: str, subdir: str = ".") -> dict[str, Any]:
        """List allowed files below a project subdirectory."""
        return _guard(lambda: tool_list_files(project_path, policy, subdir))

    @mcp.tool()
    def read_file(project_path: str, relative_path: str) -> dict[str, Any]:
        """Read one allowed UTF-8 text file from a project."""
        return _guard(lambda: tool_read_file(project_path, relative_path, policy))

    @mcp.tool()
    def list_docs(project_path: str, docs_paths: list[str] | None = None) -> dict[str, Any]:
        """List documentation files for downstream indexing."""
        return _guard(lambda: tool_list_docs(project_path, policy, docs_paths))

    @mcp.tool()
    def read_docs(project_path: str, docs_paths: list[str] | None = None) -> dict[str, Any]:
        """Read documentation files for downstream indexing."""
        return _guard(lambda: tool_read_docs(project_path, policy, docs_paths))

    @mcp.tool()
    def search_docs(
        project_path: str,
        query: str,
        docs_paths: list[str] | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Search documentation with simple case-insensitive text matching."""
        return _guard(lambda: tool_search_docs(project_path, query, policy, docs_paths, limit))

    return mcp


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only MCP server for local project context")
    parser.add_argument("--config", default=None, help=f"YAML config path, defaults to ${CONFIG_ENV} or config.yaml")
    parser.add_argument("--transport", default="streamable-http", choices=["stdio", "streamable-http"])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    args = parser.parse_args()

    mcp = build_mcp(args.config)
    if args.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(transport="streamable-http", host=args.host, port=args.port)


def _guard(call: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return call()
    except ProjectAccessError as error:
        return {"error": str(error)}
    except Exception as error:  # noqa: BLE001 - MCP users should not receive tracebacks.
        return {"error": f"unexpected server error: {error.__class__.__name__}"}


if __name__ == "__main__":
    main()
