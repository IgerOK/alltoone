# alltoone

Собирает все текстовые файлы проекта в **один файл** — готовый контекст для AI-агента
(ChatGPT, Claude, Cursor, OpenCode и т.д.). Без зависимостей, один `python`.

```
проект/                      alltoone-context.md
├── src/            ──►      ├── дерево проекта
│   ├── main.py              ├── ### `src/main.py` + код
│   └── utils.py             └── ### `src/utils.py` + код
├── README.md
└── .gitignore
```

## Зачем

- Скормить агенту весь проект целиком, не копируя файлы по одному.
- Оценить, влезет ли проект в контекстное окно модели.
- Уложиться в лимит токенов: `--max-tokens` режет результат на части по границам файлов.

## Установка

Ничего ставить не нужно, достаточно Python 3.10+:

```bash
python -m alltoone .            # из папки проекта
```

Либо как команду:

```bash
pip install -e .
alltoone .
```

## Быстрый старт

```bash
# текущая папка → stdout
python -m alltoone

# другой проект → файл
python -m alltoone ../my-app -o context.md

# только исходники и документация
python -m alltoone --include "src/**" --include "*.md" -o context.md

# XML (удобно для Claude) с лимитом 100k токенов
python -m alltoone -f xml --max-tokens 100000 -o context.xml

# сразу в буфер обмена
python -m alltoone --clipboard

# только дерево файлов, без содержимого
python -m alltoone --tree-only
```

## Что попадает в файл

1. Шапка: имя проекта, дата, число файлов, оценка токенов.
2. Дерево проекта.
3. Каждый файл: заголовок с путём и код в блоке с подсветкой языка.

Форматы: `markdown` (по умолчанию), `xml`, `plain`.

## Фильтрация

По умолчанию исключаются:

- `.git`, `node_modules`, `.venv`, `__pycache__`, `dist`, `build`, `target`, `vendor` и прочий мусор;
- lock-файлы (`package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`), минифицированные бандлы и source maps;
- бинарные файлы (по расширению и по NUL-байтам);
- файлы больше 512 КБ (`--max-file-size`).

Дополнительно читаются `.gitignore` и `.alltooneignore` — включая вложенные в подпапки.
Правила работают как в Git: `!` возвращает файл, `dir/` — только каталоги, `**` — любая глубина.

Свои правила:

```bash
python -m alltoone --exclude "*.test.ts" --exclude "docs/**"
python -m alltoone --no-gitignore            # игнорировать .gitignore
python -m alltoone --no-default-ignores      # отключить встроенный список
```

`--include` — белый список: если он задан, остаётся только то, что совпало.
Учти: `.gitignore` проверяется раньше, поэтому файл, исключённый им, не вернуть через
`--include` — используй `--no-gitignore`.

## Конфиг

Положи `alltoone.json` в корень проекта — CLI-флаги имеют приоритет:

```json
{
  "format": "markdown",
  "output": "context.md",
  "include": ["src/**", "*.md"],
  "exclude": ["*.test.ts"],
  "use_gitignore": true,
  "use_default_ignores": true,
  "max_file_size_kb": 512,
  "max_tokens": 120000,
  "tree": true,
  "files": true,
  "clipboard": false
}
```

Другой файл: `--config path/to/config.json`.

## Все опции

| Опция | Описание |
| --- | --- |
| `path` | папка или отдельный файл (по умолчанию `.`) |
| `-o, --output FILE` | куда писать (`-` = stdout) |
| `-f, --format` | `markdown`, `xml`, `plain` |
| `--include GLOB` | только совпавшие файлы (можно несколько) |
| `--exclude GLOB` | исключить совпавшие |
| `--no-gitignore` | не читать `.gitignore` / `.alltooneignore` |
| `--no-default-ignores` | без встроенного списка |
| `--max-file-size KB` | лимит размера файла, КБ |
| `--max-tokens N` | разбить результат на части по N токенов |
| `--tree-only` | только дерево |
| `--no-tree` | без дерева |
| `--clipboard` | скопировать результат |
| `--config FILE` | путь к конфигу |
| `-q, --quiet` | без сводки в stderr |

## Разбиение на части

Если проект не влезает в контекст:

```bash
python -m alltoone -o context.md --max-tokens 60000
# → context.part1.md, context.part2.md, ...
```

Режется по границам файлов, каждый файл целиком попадает в одну часть.
Каждая часть самодостаточна: шапка и дерево повторяются.

## Оценка токенов

Точного токенизатора внутри нет (это была бы зависимость на десятки мегабайт).
Используется эвристика: латиница ≈ 4 символа на токен, кириллица и CJK ≈ 1.8.
Погрешность обычно в пределах 10–20%, для решения «влезет / не влезет» этого достаточно.

## Тесты

```bash
python -m unittest discover -s tests -v
```

## Ограничения

- Только текстовые файлы; бинарники пропускаются.
- Символические ссылки на каталоги не обходятся (защита от циклов).
- Кодировки: UTF-8, UTF-16, cp1251, cp1252, latin-1. Остальные читаются как latin-1.
- `.gitignore` поддерживается в разумном подмножестве спецификации, а не побайтово.
