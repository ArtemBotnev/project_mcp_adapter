# local-project-mcp

English | [Русский](README.ru.md)

`local-project-mcp` is a read-only MCP server for local git projects. It exposes safe project context to a developer assistant: git branch/status/diff, allowed file contents, documentation discovery, simple documentation text search, and server-side SQLite RAG over documentation.

It does not call an LLM. The server builds and searches RAG indexes; the client agent receives relevant chunks and support context, then answers through the selected model.

## Install

```bash
python3.12 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
```

## Configure

Create a local config from the example:

```bash
cp config.example.yaml config.yaml
```

Set your real project roots in `config.yaml`:

```yaml
allowed_roots:
  - /home/user/projects
```

`config.yaml` is ignored by git because it contains machine-local paths. Commit only `config.example.yaml`.

Only projects inside `allowed_roots` are accepted. Secret-like names, local config suffixes, key files, databases, dependency directories, build outputs, and `.git` internals are blocked.

Support assistant ticket/user/history JSON and RAG SQLite indexes are owned by this MCP server. By default support data is read from `support_data/support/users.json`, `support_data/support/tickets.json`, and `support_data/support/histories.json`; RAG indexes are stored under `support_data/rag`. These paths can be changed with `support_data_path`, `support_users_file`, `support_tickets_file`, `support_histories_file`, and `support_rag_index_dir` in the MCP config.

The support context tool can also use a local Ollama model as a lightweight MCP-side planner. Agent settings live in `support_agent.yaml` and include Ollama URL, model, timeout, and planner prompt. The planner classifies the question and decides which facts to attach, such as code statistics, dependency declarations, text-search evidence, and RAG chunks. If Ollama is unavailable, heuristic routing is used.

## Run

By default, the server runs over HTTP on localhost:

```bash
local-project-mcp --config config.yaml
```

You can also use the project run script:

```bash
./run_server.sh
```

Equivalent explicit command:

```bash
local-project-mcp --config config.yaml --transport streamable-http --host 127.0.0.1 --port 8000
```

STDIO transport is still available:

```bash
local-project-mcp --config config.yaml --transport stdio
```

The HTTP server is a long-running process. If you start it from an environment that manages interactive sessions, you may see a message like:

```text
Session <id> idle timeout
```

That message usually means the launcher closed an idle interactive session. It is not produced by `run_server.sh` itself and does not indicate a project access error. For a background local server, run:

```bash
nohup ./run_server.sh > local-project-mcp.log 2>&1 &
```

## Assistant Connection

For HTTP clients, connect to the local streamable HTTP endpoint exposed by the SDK:

```text
http://127.0.0.1:8000/mcp
```

For a stdio MCP client, configure a server command similar to:

```json
{
  "mcpServers": {
    "local-project": {
      "command": "local-project-mcp",
      "args": ["--config", "/absolute/path/to/config.yaml", "--transport", "stdio"]
    }
  }
}
```

## Tools

### get_git_branch

Returns the current git branch.

```json
{
  "project_path": "/home/user/projects/example"
}
```

Response:

```json
{
  "project_path": "/home/user/projects/example",
  "branch": "task-31"
}
```

### get_git_status

Returns `git status --short --branch`.

```json
{
  "project_path": "/home/user/projects/example"
}
```

### get_git_diff

Returns `git diff --` or `git diff --cached --`.

```json
{
  "project_path": "/home/user/projects/example",
  "staged": false
}
```

### list_files

Returns allowed files relative to the project root.

```json
{
  "project_path": "/home/user/projects/example",
  "subdir": "."
}
```

### read_file

Reads one allowed UTF-8 file.

```json
{
  "project_path": "/home/user/projects/example",
  "relative_path": "README.md"
}
```

### list_docs

Returns documentation files for agent-side RAG.

```json
{
  "project_path": "/home/user/projects/example",
  "docs_paths": null
}
```

If `docs_paths` is omitted, the server checks `README.*`, `docs`, `project/docs`, `api`, `openapi.yaml`, `swagger.yaml`, and similar standard paths. If `docs_paths` contains `"."`, the server scans all allowed UTF-8 text files under the project, regardless of extension, while still skipping blocked directories, large files, binaries, and secret-like files.

### read_docs

Reads discovered documentation files through the same security logic as `read_file`.

```json
{
  "project_path": "/home/user/projects/example",
  "docs_paths": null
}
```

### search_docs

Simple case-insensitive text search over documentation. This is not RAG and does not use embeddings.

```json
{
  "project_path": "/home/user/projects/example",
  "query": "configuration",
  "docs_paths": null,
  "limit": 10
}
```

### rag_status

Returns server-side RAG index status for a project. The SQLite index is stored on the MCP server.

```json
{
  "project_path": "/home/user/projects/example"
}
```

### rag_build

Builds a SQLite RAG index for documentation on the MCP server.

```json
{
  "project_path": "/home/user/projects/example",
  "docs_paths": ["README.md", "docs", "support/faq"]
}
```

### rag_refresh

Refreshes the RAG index. In the first implementation this reuses the existing index unless it is missing or `force` is true.

```json
{
  "project_path": "/home/user/projects/example",
  "docs_paths": ["docs"],
  "force": false
}
```

### rag_search

Searches the server-side RAG index and returns relevant chunks.

```json
{
  "project_path": "/home/user/projects/example",
  "query": "authorization does not work",
  "limit": 5
}
```

### prepare_support_context

Returns ticket, user, and relevant documentation chunks for a support assistant. Ticket/user JSON locations are configured on the MCP server.

```json
{
  "project_path": "/home/user/projects/example",
  "ticket_id": "T-123",
  "question": "Why does authorization not work?",
  "docs_paths": ["support/faq", "docs"],
  "limit": 5
}
```

### clear_support_data

Clears all support tickets and support histories stored on the MCP server. Users are not removed.

```json
{}
```

## Security Limits

- `project_path` must resolve inside one of `allowed_roots`.
- All paths are normalized with `Path.resolve()`.
- Relative paths cannot contain `..`.
- Absolute file paths outside the project are denied.
- Symlinks escaping the project or `allowed_roots` are denied.
- Files matching `blocked_names` or `blocked_suffixes` are denied.
- Directories matching `blocked_dirs` are not traversed.
- Write/delete/move/copy operations are not implemented.
- `clear_support_data` only truncates configured support tickets and histories JSON files.
- RAG index writes go to the configured MCP support data directory, not to `project_path`.
- Git commands are read-only.
- `subprocess` is called with argument lists only, never `shell=True`.
- Git commands have a timeout.
- Large `diff/status` responses are truncated and explicitly marked.
- MCP tool errors return `{"error": "message"}` without tracebacks.

## Test

```bash
pytest
```
