from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class ServerConfig:
    allowed_roots: list[str]
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
    return ServerConfig.from_mapping(raw)


def _string_list(data: dict[str, Any], key: str) -> list[str]:
    value = data.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"config field {key!r} must be a list of strings")
    return value


def _positive_int(data: dict[str, Any], key: str, default: int) -> int:
    value = data.get(key, default)
    if not isinstance(value, int) or value <= 0:
        raise ValueError(f"config field {key!r} must be a positive integer")
    return value

