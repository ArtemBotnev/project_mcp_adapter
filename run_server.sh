#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_PATH="${LOCAL_PROJECT_MCP_CONFIG:-$ROOT_DIR/config.yaml}"
VENV_PYTHON="$ROOT_DIR/.venv/bin/python"

if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "Virtual environment not found: $ROOT_DIR/.venv" >&2
  echo "Create it with: python3.12 -m venv .venv && . .venv/bin/activate && pip install -e \".[dev]\"" >&2
  exit 1
fi

if [[ ! -f "$CONFIG_PATH" ]]; then
  echo "Config file not found: $CONFIG_PATH" >&2
  echo "Create it with: cp config.example.yaml config.yaml" >&2
  exit 1
fi

exec "$VENV_PYTHON" -m local_project_mcp.server --config "$CONFIG_PATH" "$@"
