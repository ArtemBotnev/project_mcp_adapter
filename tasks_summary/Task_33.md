# Task 33: MCP context service for support assistant

Дата: 2026-10-14

## Что сделали

MCP adapter расширен из read-only project tools до сервиса контекста для
support/project assistant.

Сервер теперь хранит runtime-данные support-режима у себя и отдает клиенту
готовый evidence pack для ответа:

```text
project files
support CRM JSON
server-side SQLite RAG
project overview
text matches
code stats
dependencies
```

## Runtime-данные на MCP

Добавлена серверная support data area:

```text
support_data/
  rag/
  support/
    users.json
    tickets.json
    histories.json
```

Папка `support_data/` добавлена в `.gitignore`.

Клиент передает только путь к исследуемому проекту и вопрос. Тикеты,
пользователи, истории и RAG-индексы хранятся на MCP-сервере.

## Конфигурация MCP

В `config.example.yaml` и локальном конфиге поддержаны настройки:

```yaml
support_data_path: support_data
support_users_file: support/users.json
support_tickets_file: support/tickets.json
support_histories_file: support/histories.json
support_rag_index_dir: rag
support_agent_config_file: support_agent.yaml
```

Добавлен example-конфиг агента планирования:

```text
support_agent.example.yaml
support_agent.yaml
```

Локальный `support_agent.yaml` добавлен в `.gitignore`.

## MCP tools

Добавлены tools:

```text
rag_status
rag_build
rag_refresh
rag_search
prepare_context
prepare_support_context
clear_support_data
```

`prepare_context` является основным tool для клиента. Он не требует тикет и
подходит для общих вопросов о проекте, продукте, архитектуре, зависимостях и
коде.

`prepare_support_context` оставлен как совместимый support-specific wrapper.

`clear_support_data` очищает tickets и histories на стороне MCP.

## Server-side RAG

RAG строится и хранится на MCP в SQLite:

```text
support_data/rag/<project-hash>.sqlite
```

Индекс не создается внутри исследуемого проекта. Исследуемый проект остается
read-only источником данных.

`docs_paths: ["."]` сканирует текстовые файлы проекта, включая код, README,
Gradle/Kotlin/Java/XML и другие UTF-8 текстовые файлы. Заблокированные директории
и бинарные файлы пропускаются.

## Evidence pack

`prepare_context` собирает структурированный контекст:

- ticket/user context, если указан ticket id;
- RAG chunks;
- `project_overview` из README, manifest, build files и дерева исходников;
- `code_stats` по классам, интерфейсам и enum;
- `dependencies` из Gradle/TOML файлов;
- `text_search` по важным словам вопроса и проектным алиасам.

Для вопросов вроде:

```text
О чём проект?
```

сервер возвращает `project_overview`.

Для вопросов вроде:

```text
Какие измерения доступны?
```

сервер возвращает совпадения из README и кода по терминам `измерения`,
`measure`, `measurement`, `sensor` и связанным словам.

## Support planner

Добавлен класс:

```text
src/local_project_mcp/support_agent.py
```

Он может использовать Ollama для планирования, какие источники контекста нужны
под конкретный вопрос:

```text
needs_rag
needs_code_stats
needs_dependencies
needs_project_overview
search_terms
```

Если агент планирования недоступен, используется эвристический план.

## Проверки

Запускались:

```bash
/home/artem/PycharmProjects/project_mcp_adapter/.venv/bin/python -m pytest tests/test_project_tools.py
```

Результат:

```text
22 passed
```

Также проверено:

- `prepare_context` работает без тикета;
- overview собирается из README;
- RAG индекс создается в `support_data/rag`, а не в исследуемом проекте;
- `docs_paths: ["."]` индексирует кодовые файлы;
- support data очищается на стороне MCP.
