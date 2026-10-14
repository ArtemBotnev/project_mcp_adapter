# local-project-mcp

[English](README.md) | Русский

`local-project-mcp` - локальный read-only MCP-сервер для git-проектов. Он дает ассистенту разработчика безопасный доступ к проектному контексту: текущая ветка, git status/diff, список разрешенных файлов, чтение отдельных файлов, список документации, простой текстовый поиск и server-side SQLite RAG по документации.

Сервер не вызывает LLM. Он строит и ищет RAG-индексы; клиентский агент получает релевантные чанки и support context, а затем отвечает через выбранную модель.

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

JSON с тикетами, пользователями, историями и SQLite RAG-индексы принадлежат MCP-серверу. По умолчанию support-данные читаются из `support_data/support/users.json`, `support_data/support/tickets.json` и `support_data/support/histories.json`; RAG-индексы хранятся в `support_data/rag`. Эти пути можно изменить через `support_data_path`, `support_users_file`, `support_tickets_file`, `support_histories_file` и `support_rag_index_dir` в конфиге MCP.

Support context tool также может использовать локальную Ollama-модель как лёгкий planner на стороне MCP. Настройки агента живут в `support_agent.yaml`: URL Ollama, модель, timeout и planner prompt. Planner классифицирует вопрос и решает, какие факты приложить: статистику кода, зависимости, text-search evidence и RAG chunks. Если Ollama недоступна, используется эвристический routing.

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
Если в `docs_paths` указан `"."`, сервер обходит все разрешённые UTF-8 текстовые файлы проекта независимо от расширения, но всё равно пропускает заблокированные директории, большие файлы, бинарники и секретоподобные файлы.

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

### rag_status

Возвращает статус server-side RAG-индекса для проекта. SQLite-индекс хранится на MCP-сервере.

```json
{
  "project_path": "/home/user/projects/example"
}
```

### rag_build

Строит SQLite RAG-индекс по документации на MCP-сервере.

```json
{
  "project_path": "/home/user/projects/example",
  "docs_paths": ["README.md", "docs", "support/faq"]
}
```

### rag_refresh

Обновляет RAG-индекс. В первой реализации использует существующий индекс, если он есть и `force` не включён.

```json
{
  "project_path": "/home/user/projects/example",
  "docs_paths": ["docs"],
  "force": false
}
```

### rag_search

Ищет по server-side RAG-индексу и возвращает релевантные чанки.

```json
{
  "project_path": "/home/user/projects/example",
  "query": "не работает авторизация",
  "limit": 5
}
```

### prepare_support_context

Возвращает тикет, пользователя и релевантные чанки документации для support assistant. Пути к JSON с тикетами и пользователями настраиваются на стороне MCP-сервера.

```json
{
  "project_path": "/home/user/projects/example",
  "ticket_id": "T-123",
  "question": "Почему не работает авторизация?",
  "docs_paths": ["support/faq", "docs"],
  "limit": 5
}
```

### clear_support_data

Очищает все support-тикеты и support-истории на MCP-сервере. Пользователи не удаляются.

```json
{}
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
- `clear_support_data` только обнуляет настроенные JSON-файлы тикетов и историй.
- RAG-индекс пишется в настроенную support data директорию MCP, а не в `project_path`.
- Git-команды только read-only.
- `subprocess` вызывается только списком аргументов, без `shell=True`.
- У git-команд есть timeout.
- Большие `diff/status` ответы обрезаются и явно помечаются.
- Ошибки MCP tools возвращаются как `{"error": "message"}` без traceback.

## Тесты

```bash
pytest
```
