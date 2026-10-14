from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class ServerConfig:
    allowed_roots: list[str]
    support_data_path: str = "support_data"
    support_users_file: str = "support/users.json"
    support_tickets_file: str = "support/tickets.json"
    support_histories_file: str = "support/histories.json"
    support_rag_index_dir: str = "rag"
    support_agent_config_file: str = "support_agent.yaml"
    blocked_names: list[str] = field(default_factory=list)
    blocked_suffixes: list[str] = field(default_factory=list)
    blocked_dirs: list[str] = field(default_factory=list)
    max_file_size_bytes: int = 1_000_000
    max_list_files: int = 5_000
    max_diff_bytes: int = 200_000

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "ServerConfig":
        allowed_roots = data.get("allowed_roots") or []
        if not isinstance(allowed_roots, list) or not allowed_roots:
            raise ValueError("config must define a non-empty allowed_roots list")

        return cls(
            allowed_roots=_string_list(data, "allowed_roots"),
            support_data_path=_optional_string(data, "support_data_path", "support_data"),
            support_users_file=_optional_string(data, "support_users_file", "support/users.json"),
            support_tickets_file=_optional_string(data, "support_tickets_file", "support/tickets.json"),
            support_histories_file=_optional_string(data, "support_histories_file", "support/histories.json"),
            support_rag_index_dir=_optional_string(data, "support_rag_index_dir", "rag"),
            support_agent_config_file=_optional_string(data, "support_agent_config_file", "support_agent.yaml"),
            blocked_names=_string_list(data, "blocked_names"),
            blocked_suffixes=_string_list(data, "blocked_suffixes"),
            blocked_dirs=_string_list(data, "blocked_dirs"),
            max_file_size_bytes=_positive_int(data, "max_file_size_bytes", 1_000_000),
            max_list_files=_positive_int(data, "max_list_files", 5_000),
            max_diff_bytes=_positive_int(data, "max_diff_bytes", 200_000),
        )


def load_config(path: str | Path) -> ServerConfig:
    config_path = Path(path).expanduser()
    with config_path.open("r", encoding="utf-8") as file:
        raw = yaml.safe_load(file) or {}
    if not isinstance(raw, dict):
        raise ValueError("config root must be a mapping")
    config = ServerConfig.from_mapping(raw)
    support_data_path = Path(config.support_data_path).expanduser()
    if not support_data_path.is_absolute():
        support_data_path = config_path.parent / support_data_path
    support_agent_config_file = Path(config.support_agent_config_file).expanduser()
    if not support_agent_config_file.is_absolute():
        support_agent_config_file = config_path.parent / support_agent_config_file
    return ServerConfig(
        allowed_roots=config.allowed_roots,
        support_data_path=str(support_data_path.resolve()),
        support_users_file=config.support_users_file,
        support_tickets_file=config.support_tickets_file,
        support_histories_file=config.support_histories_file,
        support_rag_index_dir=config.support_rag_index_dir,
        support_agent_config_file=str(support_agent_config_file.resolve()),
        blocked_names=config.blocked_names,
        blocked_suffixes=config.blocked_suffixes,
        blocked_dirs=config.blocked_dirs,
        max_file_size_bytes=config.max_file_size_bytes,
        max_list_files=config.max_list_files,
        max_diff_bytes=config.max_diff_bytes,
    )


def _string_list(data: dict[str, Any], key: str) -> list[str]:
    value = data.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"config field {key!r} must be a list of strings")
    return value


def _optional_string(data: dict[str, Any], key: str, default: str) -> str:
    value = data.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"config field {key!r} must be a non-empty string")
    return value.strip()


def _positive_int(data: dict[str, Any], key: str, default: int) -> int:
    value = data.get(key, default)
    if not isinstance(value, int) or value <= 0:
        raise ValueError(f"config field {key!r} must be a positive integer")
    return value
