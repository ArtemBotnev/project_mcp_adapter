from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import struct
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from local_project_mcp.project_tools import read_docs
from local_project_mcp.security import ProjectAccessError, ProjectSecurityPolicy


DEFAULT_INDEX_DIR = Path("rag")
DEFAULT_TOP_K = 5
MIN_RAG_SCORE = 0.18
VECTOR_SIZE = 256
CHUNK_WORDS = 220
CHUNK_OVERLAP_WORDS = 40
WORD_PATTERN = re.compile(r"[\wа-яА-ЯёЁ]+", re.UNICODE)
CLASS_PATTERN = re.compile(r"\b(?:class|interface|enum(?:\s+class)?)\s+([A-Za-z_][A-Za-z0-9_]*)")
DEPENDENCY_PATTERN = re.compile(r"\b(?:implementation|api|compileOnly|runtimeOnly|testImplementation)\s*\(([^)]+)\)")
PROJECT_OVERVIEW_TERMS = (
    "о чём",
    "о чем",
    "что за проект",
    "что это",
    "назначение",
    "overview",
    "about",
    "summary",
)
TERM_ALIASES = {
    "измер": ("measure", "measurement"),
    "датчик": ("sensor",),
    "сенсор": ("sensor",),
    "температ": ("temperature",),
    "влаж": ("humidity",),
    "давлен": ("pressure",),
    "част": ("refresh", "update", "interval", "period", "time"),
    "получ": ("get", "load", "fetch", "repository"),
    "устройств": ("device",),
    "погод": ("weather",),
}


@dataclass(frozen=True)
class RagChunk:
    chunk_id: str
    relative_path: str
    title: str
    start_word: int
    end_word: int
    text: str
    embedding: list[float]


class SupportPlanner(Protocol):
    def plan(self, question: str, fallback: dict[str, object]) -> dict[str, object]:
        ...


def rag_status(
    project_path: str,
    policy: ProjectSecurityPolicy,
    index_dir: str | None = None,
    index_base_path: str | None = None,
) -> dict[str, object]:
    project = policy.validate_project_path(project_path)
    index_file = _index_file(project, index_dir, policy, index_base_path)
    if not index_file.exists():
        return {
            "project_path": str(project),
            "index_file": str(index_file),
            "exists": False,
            "documents": 0,
            "chunks": 0,
            "created_at": None,
        }

    with sqlite3.connect(index_file) as connection:
        metadata = dict(connection.execute("select key, value from metadata").fetchall())
        chunks = int(connection.execute("select count(*) from chunks").fetchone()[0])
    return {
        "project_path": str(project),
        "index_file": str(index_file),
        "exists": True,
        "documents": int(metadata.get("documents", "0")),
        "chunks": chunks,
        "created_at": metadata.get("created_at"),
    }


def rag_build(
    project_path: str,
    policy: ProjectSecurityPolicy,
    docs_paths: list[str] | None = None,
    index_dir: str | None = None,
    index_base_path: str | None = None,
) -> dict[str, object]:
    project = policy.validate_project_path(project_path)
    documents_result = read_docs(str(project), policy, docs_paths)
    documents = list(documents_result["documents"])
    chunks: list[RagChunk] = []
    for document in documents:
        chunks.extend(_chunk_document(document))
    if not chunks:
        raise ProjectAccessError("no documentation chunks were produced")

    index_file = _index_file(project, index_dir, policy, index_base_path)
    _write_index(index_file, chunks, len(documents))
    return {
        "project_path": str(project),
        "index_file": str(index_file),
        "documents": len(documents),
        "chunks": len(chunks),
        "skipped": documents_result["skipped"],
    }


def rag_refresh(
    project_path: str,
    policy: ProjectSecurityPolicy,
    docs_paths: list[str] | None = None,
    index_dir: str | None = None,
    force: bool = False,
    index_base_path: str | None = None,
) -> dict[str, object]:
    result = rag_build(project_path, policy, docs_paths, index_dir, index_base_path)
    result["status"] = "updated"
    return result


def rag_search(
    project_path: str,
    query: str,
    policy: ProjectSecurityPolicy,
    index_dir: str | None = None,
    limit: int = DEFAULT_TOP_K,
    index_base_path: str | None = None,
    planner: SupportPlanner | None = None,
) -> dict[str, object]:
    clean_query = query.strip()
    if not clean_query:
        raise ProjectAccessError("query must not be empty")

    project = policy.validate_project_path(project_path)
    index_file = _index_file(project, index_dir, policy, index_base_path)
    if not index_file.exists():
        raise ProjectAccessError("RAG index does not exist; call rag_build first")

    query_embedding = _embed_text(clean_query)
    max_results = max(1, min(limit, 20))
    chunks = _load_chunks(index_file)
    matches = sorted(
        (
            {
                "chunk_id": chunk.chunk_id,
                "relative_path": chunk.relative_path,
                "title": chunk.title,
                "score": _cosine_similarity(query_embedding, chunk.embedding),
                "text": chunk.text,
            }
            for chunk in chunks
        ),
        key=lambda item: float(item["score"]),
        reverse=True,
    )
    matches = [match for match in matches if float(match["score"]) >= MIN_RAG_SCORE]
    return {
        "project_path": str(project),
        "query": clean_query,
        "matches": matches[:max_results],
    }


def prepare_support_context(
    project_path: str,
    ticket_id: str,
    question: str,
    policy: ProjectSecurityPolicy,
    support_data_path: str | None = None,
    users_file: str | None = None,
    tickets_file: str | None = None,
    docs_paths: list[str] | None = None,
    index_dir: str | None = None,
    limit: int = DEFAULT_TOP_K,
    index_base_path: str | None = None,
    planner: SupportPlanner | None = None,
) -> dict[str, object]:
    project = policy.validate_project_path(project_path)
    support_project = policy.validate_project_path(support_data_path) if support_data_path else project
    status = rag_status(str(project), policy, index_dir, index_base_path)
    if not status["exists"]:
        rag_build(str(project), policy, docs_paths, index_dir, index_base_path)

    ticket = _find_support_record(support_project, policy, tickets_file or "support/tickets.json", ticket_id)
    user_id = _record_user_id(ticket)
    user = _find_support_record(support_project, policy, users_file or "support/users.json", user_id) if user_id else None
    plan = plan_support_context(question, planner)
    search = (
        rag_search(str(project), question, policy, index_dir, limit, index_base_path)
        if plan.get("needs_rag")
        else {"matches": []}
    )
    code_stats = collect_project_code_stats(str(project), policy, docs_paths)
    answer_facts = collect_answer_facts(str(project), question, policy, docs_paths, plan, code_stats)
    return {
        "project_path": str(project),
        "support_data_path": str(support_project),
        "ticket": ticket,
        "user": user,
        "support_context_plan": plan,
        "answer_facts": answer_facts,
        "project_code_stats": code_stats,
        "chunks": search["matches"],
        "rag": rag_status(str(project), policy, index_dir, index_base_path),
    }


def plan_support_context(question: str, planner: SupportPlanner | None = None) -> dict[str, object]:
    fallback = _heuristic_plan(question)
    if planner is None:
        return fallback
    return planner.plan(question, fallback)


def collect_answer_facts(
    project_path: str,
    question: str,
    policy: ProjectSecurityPolicy,
    docs_paths: list[str] | None,
    plan: dict[str, object],
    code_stats: dict[str, object],
) -> list[dict[str, object]]:
    facts: list[dict[str, object]] = []
    if plan.get("needs_code_stats"):
        facts.append(
            {
                "type": "code_stats",
                "summary": f"Project contains {code_stats['class_count']} class/interface/enum declarations.",
                "data": code_stats,
            }
        )
    if plan.get("needs_dependencies"):
        dependencies = collect_project_dependencies(project_path, policy, docs_paths)
        facts.append(
            {
                "type": "dependencies",
                "summary": f"Project dependency declarations found: {len(dependencies['dependencies'])}.",
                "data": dependencies,
            }
        )
    if plan.get("needs_project_overview"):
        overview = collect_project_overview(project_path, policy, docs_paths)
        facts.append(
            {
                "type": "project_overview",
                "summary": overview.get("summary") or "Project overview collected from repository files.",
                "data": overview,
            }
        )
    for term in _question_search_terms(question, plan)[:10]:
        matches = search_text_documents(project_path, term, policy, docs_paths, limit=10)
        if matches:
            facts.append(
                {
                    "type": "text_search",
                    "query": term,
                    "matches": matches,
                }
            )
    return facts


def collect_project_overview(
    project_path: str,
    policy: ProjectSecurityPolicy,
    docs_paths: list[str] | None = None,
) -> dict[str, object]:
    documents_result = read_docs(str(policy.validate_project_path(project_path)), policy, docs_paths)
    documents = list(documents_result["documents"])
    relative_paths = [str(document["relative_path"]) for document in documents]
    readme = _first_document_excerpt(documents, ("README.md", "readme.md"), max_chars=1600)
    manifest = _first_document_excerpt(documents, ("AndroidManifest.xml",), max_chars=800)
    build_files = [
        path
        for path in relative_paths
        if path.endswith(("build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts", "libs.versions.toml"))
    ][:20]
    source_files = [
        path
        for path in relative_paths
        if path.endswith((".kt", ".java", ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".swift"))
    ]
    summary = _first_non_heading_line(readme) or _infer_project_summary(relative_paths, build_files, manifest)
    return {
        "summary": summary,
        "readme_excerpt": readme,
        "manifest_excerpt": manifest,
        "build_files": build_files,
        "source_files_count": len(source_files),
        "source_files_sample": source_files[:40],
        "documents_scanned": len(documents),
        "skipped": documents_result["skipped"],
    }


def collect_project_code_stats(
    project_path: str,
    policy: ProjectSecurityPolicy,
    docs_paths: list[str] | None = None,
) -> dict[str, object]:
    project = policy.validate_project_path(project_path)
    documents_result = read_docs(str(project), policy, docs_paths)
    classes: list[dict[str, str]] = []
    for document in documents_result["documents"]:
        relative_path = str(document["relative_path"])
        for match in CLASS_PATTERN.finditer(str(document["content"])):
            classes.append(
                {
                    "name": match.group(1),
                    "relative_path": relative_path,
                }
            )
    return {
        "class_count": len(classes),
        "classes": classes,
        "scanned_documents": len(documents_result["documents"]),
        "skipped": documents_result["skipped"],
    }


def collect_project_dependencies(
    project_path: str,
    policy: ProjectSecurityPolicy,
    docs_paths: list[str] | None = None,
) -> dict[str, object]:
    documents_result = read_docs(str(policy.validate_project_path(project_path)), policy, docs_paths)
    dependencies: list[dict[str, str]] = []
    for document in documents_result["documents"]:
        relative_path = str(document["relative_path"])
        if "gradle" not in relative_path and not relative_path.endswith(".toml"):
            continue
        for match in DEPENDENCY_PATTERN.finditer(str(document["content"])):
            dependencies.append({"declaration": match.group(0), "relative_path": relative_path})
    return {
        "dependencies": dependencies,
        "scanned_documents": len(documents_result["documents"]),
    }


def search_text_documents(
    project_path: str,
    query: str,
    policy: ProjectSecurityPolicy,
    docs_paths: list[str] | None = None,
    limit: int = 10,
) -> list[dict[str, object]]:
    clean_query = query.strip().casefold()
    if not clean_query:
        return []
    documents_result = read_docs(str(policy.validate_project_path(project_path)), policy, docs_paths)
    matches: list[dict[str, object]] = []
    for document in documents_result["documents"]:
        content = str(document["content"])
        index = content.casefold().find(clean_query)
        if index < 0:
            continue
        matches.append(
            {
                "relative_path": document["relative_path"],
                "snippet": _snippet(content, index),
            }
        )
        if len(matches) >= limit:
            break
    return matches


def clear_support_data(
    policy: ProjectSecurityPolicy,
    support_data_path: str,
    tickets_file: str = "support/tickets.json",
    histories_file: str = "support/histories.json",
) -> dict[str, object]:
    support_project = policy.validate_project_path(support_data_path)
    cleared_files = [
        _clear_json_array_file(support_project, policy, tickets_file),
        _clear_json_array_file(support_project, policy, histories_file),
    ]
    return {
        "support_data_path": str(support_project),
        "cleared": cleared_files,
    }


def _index_file(
    project: Path,
    index_dir: str | None,
    policy: ProjectSecurityPolicy,
    index_base_path: str | None = None,
) -> Path:
    if index_base_path:
        base_dir = Path(index_base_path).expanduser().resolve()
        if not base_dir.is_absolute():
            raise ProjectAccessError("index_base_path must be absolute")
        if index_dir:
            relative_index_dir = Path(index_dir)
            if relative_index_dir.is_absolute() or any(part == ".." for part in relative_index_dir.parts):
                raise ProjectAccessError("index_dir must be relative and must not contain '..'")
            base_dir = base_dir / relative_index_dir
    elif index_dir:
        base_dir = Path(index_dir).expanduser()
        if base_dir.is_absolute():
            raise ProjectAccessError("index_dir must be relative to project_path")
        base_dir = policy.resolve_relative_path(project, base_dir)
    else:
        base_dir = project / DEFAULT_INDEX_DIR
    project_key = hashlib.sha256(str(project).encode("utf-8")).hexdigest()[:16]
    return base_dir.resolve() / f"{project_key}.sqlite"


def _chunk_document(document: dict[str, object]) -> list[RagChunk]:
    relative_path = str(document["relative_path"])
    content = str(document["content"]).strip()
    words = WORD_PATTERN.findall(content)
    if not words:
        return []

    chunks: list[RagChunk] = []
    step = max(1, CHUNK_WORDS - CHUNK_OVERLAP_WORDS)
    start = 0
    index = 0
    while start < len(words):
        end = min(start + CHUNK_WORDS, len(words))
        text = " ".join(words[start:end])
        chunks.append(
            RagChunk(
                chunk_id=f"{relative_path}:{index:05d}",
                relative_path=relative_path,
                title=_title_for(relative_path, content),
                start_word=start,
                end_word=end,
                text=text,
                embedding=_embed_text(text),
            )
        )
        if end >= len(words):
            break
        start += step
        index += 1
    return chunks


def _write_index(index_file: Path, chunks: list[RagChunk], documents_count: int) -> None:
    index_file.parent.mkdir(parents=True, exist_ok=True)
    created_at = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(index_file) as connection:
        connection.execute(
            """
            create table if not exists chunks (
                id integer primary key autoincrement,
                chunk_id text not null,
                relative_path text not null,
                title text not null,
                start_word integer not null,
                end_word integer not null,
                embedding_blob blob not null,
                text_content text not null,
                created_at text not null
            )
            """
        )
        connection.execute(
            """
            create table if not exists metadata (
                key text primary key,
                value text not null
            )
            """
        )
        connection.execute("delete from chunks")
        connection.execute("delete from metadata")
        connection.executemany(
            """
            insert into chunks (
                chunk_id, relative_path, title, start_word, end_word,
                embedding_blob, text_content, created_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    chunk.chunk_id,
                    chunk.relative_path,
                    chunk.title,
                    chunk.start_word,
                    chunk.end_word,
                    _encode_embedding(chunk.embedding),
                    chunk.text,
                    created_at,
                )
                for chunk in chunks
            ],
        )
        connection.executemany(
            "insert into metadata (key, value) values (?, ?)",
            [
                ("created_at", created_at),
                ("documents", str(documents_count)),
                ("chunks", str(len(chunks))),
            ],
        )


def _load_chunks(index_file: Path) -> list[RagChunk]:
    with sqlite3.connect(index_file) as connection:
        rows = connection.execute(
            """
            select chunk_id, relative_path, title, start_word, end_word, embedding_blob, text_content
            from chunks
            """
        ).fetchall()
    return [
        RagChunk(
            chunk_id=str(row[0]),
            relative_path=str(row[1]),
            title=str(row[2]),
            start_word=int(row[3]),
            end_word=int(row[4]),
            embedding=_decode_embedding(row[5]),
            text=str(row[6]),
        )
        for row in rows
    ]


def _find_support_record(
    project: Path,
    policy: ProjectSecurityPolicy,
    relative_path: str,
    record_id: str | None,
) -> dict[str, Any] | None:
    if not record_id:
        return None
    record_file = policy.resolve_relative_path(project, relative_path)
    record_file = policy.assert_read_allowed(project, record_file)
    data = json.loads(record_file.read_text(encoding="utf-8"))
    records = _json_records(data)
    clean_id = record_id.strip()
    for record in records:
        for key in ("id", "ticket_id", "user_id", "customer_id"):
            value = record.get(key)
            if isinstance(value, str) and value.strip() == clean_id:
                return record
    return None


def _json_records(data: object) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        for key in ("users", "tickets", "items"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            data = list(data.values())
    if not isinstance(data, list):
        raise ProjectAccessError("support JSON must contain an array or object records")
    records = []
    for item in data:
        if not isinstance(item, dict):
            raise ProjectAccessError("support JSON records must be objects")
        records.append(item)
    return records


def _record_user_id(record: dict[str, Any] | None) -> str | None:
    if record is None:
        return None
    for key in ("user_id", "customer_id"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _heuristic_plan(question: str) -> dict[str, object]:
    normalized = question.casefold()
    needs_project_overview = any(term in normalized for term in PROJECT_OVERVIEW_TERMS)
    needs_code_stats = any(term in normalized for term in ("сколько", "количество", "count", "how many"))
    needs_dependencies = any(term in normalized for term in ("зависимост", "dependency", "dependencies", "gradle"))
    search_terms = []
    if any(term in normalized for term in ("найди", "где используется", "используется", "find", "usage")):
        search_terms = _important_terms(question)
    needs_rag = not (needs_code_stats or needs_dependencies or needs_project_overview)
    return {
        "source": "heuristic",
        "needs_rag": needs_rag,
        "needs_code_stats": needs_code_stats,
        "needs_dependencies": needs_dependencies,
        "needs_project_overview": needs_project_overview,
        "search_terms": search_terms,
        "reason": "heuristic routing",
    }


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _important_terms(question: str) -> list[str]:
    stop_words = {
        "где",
        "что",
        "как",
        "какие",
        "какой",
        "какая",
        "когда",
        "можно",
        "нужно",
        "надо",
        "проект",
        "проекта",
        "вообще",
        "доступны",
        "найди",
        "используется",
        "используют",
        "find",
        "usage",
        "where",
        "is",
        "the",
        "about",
    }
    return [
        token
        for token in WORD_PATTERN.findall(question)
        if len(token) > 2 and token.casefold() not in stop_words
    ][:5]


def _question_search_terms(question: str, plan: dict[str, object]) -> list[str]:
    terms = [*(_string_list(plan.get("search_terms"))), *_important_terms(question)]
    normalized = question.casefold()
    for marker, aliases in TERM_ALIASES.items():
        if marker in normalized:
            terms.extend(aliases)
    return _unique_terms(terms)


def _unique_terms(terms: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for term in terms:
        clean_term = term.strip()
        if not clean_term:
            continue
        key = clean_term.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(clean_term)
    return result


def _first_document_excerpt(
    documents: list[dict[str, object]],
    suffixes: tuple[str, ...],
    max_chars: int,
) -> str:
    for document in documents:
        relative_path = str(document["relative_path"]).casefold()
        if any(relative_path.endswith(suffix.casefold()) for suffix in suffixes):
            return str(document["content"]).strip()[:max_chars]
    return ""


def _first_non_heading_line(text: str) -> str:
    for line in text.splitlines():
        clean_line = line.strip()
        if not clean_line or clean_line.startswith("#"):
            continue
        return clean_line
    return ""


def _infer_project_summary(relative_paths: list[str], build_files: list[str], manifest: str) -> str:
    if manifest:
        return "Android project with an application manifest."
    if any(path.endswith((".gradle", ".gradle.kts")) for path in build_files):
        return "Gradle-based project."
    if any(path.endswith(".py") for path in relative_paths):
        return "Python project."
    if any(path.endswith((".js", ".ts", ".tsx", ".jsx")) for path in relative_paths):
        return "JavaScript/TypeScript project."
    return "Project overview inferred from repository files."


def _clear_json_array_file(project: Path, policy: ProjectSecurityPolicy, relative_path: str) -> str:
    path = policy.resolve_relative_path(project, relative_path)
    if policy.is_blocked_path(path, project):
        raise ProjectAccessError(f"access to blocked path is denied: {relative_path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[]\n", encoding="utf-8")
    return path.relative_to(project).as_posix()


def _embed_text(text: str) -> list[float]:
    vector = [0.0] * VECTOR_SIZE
    for token in WORD_PATTERN.findall(text.casefold()):
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        bucket = int.from_bytes(digest[:4], "big") % VECTOR_SIZE
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[bucket] += sign
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


def _snippet(content: str, index: int, radius: int = 140) -> str:
    start = max(0, index - radius)
    end = min(len(content), index + radius)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(content) else ""
    return prefix + content[start:end].replace("\n", " ").strip() + suffix


def _encode_embedding(embedding: list[float]) -> bytes:
    return struct.pack(f"{len(embedding)}f", *embedding)


def _decode_embedding(blob: bytes) -> list[float]:
    return list(struct.unpack(f"{len(blob) // 4}f", blob))


def _title_for(relative_path: str, content: str) -> str:
    for line in content.splitlines():
        clean = line.strip()
        if clean.startswith("#"):
            return clean.lstrip("#").strip() or relative_path
    return relative_path
