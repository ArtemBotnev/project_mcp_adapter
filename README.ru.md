# local-project-mcp

[English](README.md) | Русский

`local-project-mcp` - локальный read-only MCP-сервер для git-проектов. Он дает ассистенту разработчика безопасный доступ к проектному контексту: текущая ветка, git status/diff, список разрешенных файлов, чтение отдельных файлов, список документации и простой текстовый поиск по документации.

Сервер не строит RAG-индекс и не вызывает LLM. RAG остается на стороне агента: агент запрашивает у MCP файлы и контекст, строит свой SQLite/RAG-индекс и отвечает через выбранную модель.

## Установка

```bash
python3.12 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
```

## Конфигурация

Создайте локальный конфиг из примера:

```bash
cp config.example.yaml config.yaml
```

В `config.yaml` укажите реальные корни проектов:

```yaml
allowed_roots:
  - /home/user/projects
```

Файл `config.yaml` добавлен в `.gitignore`, потому что содержит локальные пути конкретной машины. В репозиторий должен попадать только `config.example.yaml`.

Сервер принимает только проекты внутри `allowed_roots`. Имена секретов, локальные конфиги, ключи, базы данных, зависимости, build-директории и `.git`-внутренности заблокированы.

## Запуск

По умолчанию сервер запускается по HTTP на localhost:

```bash
local-project-mcp --config config.yaml
```

Также можно использовать скрипт запуска из корня проекта:

```bash
./run_server.sh
```

Эквивалентная явная команда:

```bash
local-project-mcp --config config.yaml --transport streamable-http --host 127.0.0.1 --port 8000
```

STDIO-транспорт тоже доступен:

```bash
local-project-mcp --config config.yaml --transport stdio
```

HTTP-сервер - это долгоживущий процесс. Если запускать его из среды, которая управляет интерактивными сессиями, можно увидеть сообщение вида:

```text
Session <id> idle timeout
```

Обычно это значит, что launcher закрыл неактивную интерактивную сессию. Это сообщение не генерируется самим `run_server.sh` и не означает ошибку доступа к проекту. Для фонового локального запуска используйте:

```bash
nohup ./run_server.sh > local-project-mcp.log 2>&1 &
```

## Подключение К Ассистенту

Для HTTP-клиента подключайте MCP server по локальному streamable HTTP endpoint, который поднимает SDK:

```text
http://127.0.0.1:8000/mcp
```

Для stdio MCP-клиента конфигурация может выглядеть так:

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

## Доступные Tools

### get_git_branch

Возвращает текущую git-ветку.

```json
{
  "project_path": "/home/user/projects/example"
}
```

Ответ:

```json
{
  "project_path": "/home/user/projects/example",
  "branch": "task-31"
}
```

### get_git_status

Возвращает `git status --short --branch`.

```json
{
  "project_path": "/home/user/projects/example"
}
```

### get_git_diff

Возвращает `git diff --` или `git diff --cached --`.

```json
{
  "project_path": "/home/user/projects/example",
  "staged": false
}
```

### list_files

Возвращает список разрешенных файлов относительно корня проекта.

```json
{
  "project_path": "/home/user/projects/example",
  "subdir": "."
}
```

### read_file

Читает один разрешенный UTF-8 файл.

```json
{
  "project_path": "/home/user/projects/example",
  "relative_path": "README.md"
}
```

### list_docs

Возвращает список документационных файлов для RAG на стороне агента.

```json
{
  "project_path": "/home/user/projects/example",
  "docs_paths": null
}
```

Если `docs_paths` не передан, используются `README.*`, `docs`, `project/docs`, `api`, `openapi.yaml`, `swagger.yaml` и похожие стандартные пути.

### read_docs

Читает найденные документационные файлы через ту же security-логику, что и `read_file`.

```json
{
  "project_path": "/home/user/projects/example",
  "docs_paths": null
}
```

### search_docs

Простой case-insensitive текстовый поиск по документации. Это не RAG и не embeddings.

```json
{
  "project_path": "/home/user/projects/example",
  "query": "configuration",
  "docs_paths": null,
  "limit": 10
}
```

## Ограничения Безопасности

- `project_path` обязан находиться внутри одного из `allowed_roots`.
- Все пути нормализуются через `Path.resolve()`.
- Относительные пути не могут содержать `..`.
- Абсолютные пути к файлам вне проекта запрещены.
- Symlink, который ведет наружу проекта или наружу `allowed_roots`, запрещен.
- Файлы из `blocked_names` и файлы с суффиксами из `blocked_suffixes` не читаются.
- Директории из `blocked_dirs` не обходятся.
- Операции write/delete/move/copy не реализованы.
- Git-команды только read-only.
- `subprocess` вызывается только списком аргументов, без `shell=True`.
- У git-команд есть timeout.
- Большие `diff/status` ответы обрезаются и явно помечаются.
- Ошибки MCP tools возвращаются как `{"error": "message"}` без traceback.

## Тесты

```bash
pytest
```
