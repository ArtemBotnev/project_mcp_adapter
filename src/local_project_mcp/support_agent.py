from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

import yaml


DEFAULT_PLANNER_PROMPT = """
Classify a support/project question for tool use.
Return only JSON with keys:
- needs_rag: boolean
- needs_code_stats: boolean
- needs_dependencies: boolean
- needs_project_overview: boolean
- search_terms: array of strings
- reason: string

Use needs_code_stats for count/list questions about classes, interfaces, files, screens, ViewModels.
Use needs_dependencies for Gradle/dependency questions.
Use needs_project_overview for broad questions about project purpose, features, architecture, or tech stack.
Use search_terms for find/usage questions.
""".strip()


@dataclass(frozen=True)
class SupportAgentConfig:
    enabled: bool = False
    ollama_base_url: str = "http://127.0.0.1:11434"
    model: str = "llama3.1:8b"
    timeout_seconds: int = 5
    planner_prompt: str = DEFAULT_PLANNER_PROMPT


class SupportContextPlanner:
    def __init__(self, config: SupportAgentConfig) -> None:
        self._config = config

    def plan(self, question: str, fallback: dict[str, object]) -> dict[str, object]:
        if not self._config.enabled:
            return fallback
        try:
            planned = self._ollama_plan(question)
        except (OSError, TimeoutError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return {**fallback, "planner_error": "support agent unavailable; heuristic plan used"}

        return {
            "source": "ollama",
            "needs_rag": bool(planned.get("needs_rag", fallback["needs_rag"])),
            "needs_code_stats": bool(planned.get("needs_code_stats", fallback["needs_code_stats"])),
            "needs_dependencies": bool(planned.get("needs_dependencies", fallback["needs_dependencies"])),
            "needs_project_overview": bool(
                planned.get("needs_project_overview", fallback.get("needs_project_overview", False))
            ),
            "search_terms": _string_list(planned.get("search_terms")) or fallback["search_terms"],
            "reason": str(planned.get("reason") or ""),
        }

    def _ollama_plan(self, question: str) -> dict[str, object]:
        payload = {
            "model": self._config.model,
            "messages": [
                {
                    "role": "user",
                    "content": f"{self._config.planner_prompt}\n\nQuestion: {question}",
                }
            ],
            "stream": False,
            "options": {"temperature": 0, "num_predict": 256},
        }
        request = Request(
            f"{self._config.ollama_base_url.rstrip('/')}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=self._config.timeout_seconds) as response:
            response_data = json.loads(response.read().decode("utf-8"))
        content = response_data["message"]["content"]
        return _parse_json_object(content)


def load_support_agent_config(path: str | Path | None) -> SupportAgentConfig:
    if path is None:
        return SupportAgentConfig()

    config_path = Path(path).expanduser()
    if not config_path.exists():
        return SupportAgentConfig()

    with config_path.open("r", encoding="utf-8") as file:
        raw = yaml.safe_load(file) or {}
    if not isinstance(raw, dict):
        raise ValueError("support agent config root must be a mapping")

    section = raw.get("support_agent", raw)
    if not isinstance(section, dict):
        raise ValueError("support_agent config section must be a mapping")

    prompt = str(section.get("planner_prompt") or DEFAULT_PLANNER_PROMPT).strip()
    return SupportAgentConfig(
        enabled=_bool_option(section, "enabled", False),
        ollama_base_url=_string_option(section, "ollama_base_url", "http://127.0.0.1:11434"),
        model=_string_option(section, "model", "llama3.1:8b"),
        timeout_seconds=_positive_int(section, "timeout_seconds", 5),
        planner_prompt=prompt or DEFAULT_PLANNER_PROMPT,
    )


def _parse_json_object(text: str) -> dict[str, object]:
    clean_text = text.strip()
    if clean_text.startswith("```"):
        clean_text = clean_text.strip("`")
        clean_text = clean_text.removeprefix("json").strip()
    start = clean_text.find("{")
    end = clean_text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("planner returned no JSON object")
    parsed = json.loads(clean_text[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("planner JSON must be an object")
    return parsed


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _string_option(data: dict[str, Any], key: str, default: str) -> str:
    value = data.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"support agent config field {key!r} must be a non-empty string")
    return value.strip()


def _bool_option(data: dict[str, Any], key: str, default: bool) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"support agent config field {key!r} must be boolean")
    return value


def _positive_int(data: dict[str, Any], key: str, default: int) -> int:
    value = data.get(key, default)
    if not isinstance(value, int) or value <= 0:
        raise ValueError(f"support agent config field {key!r} must be a positive integer")
    return value
