# Выпуск

Локальный чеклист. Ни один шаг не делает push: публикация — отдельная команда и
отдельное решение.

## Выпустить версию

1. `python -m unittest discover -s tests` — зелёный (тесты + node-проверки плагина).
2. Поднять одну и ту же версию в `pyproject.toml` и `alltoone/__init__.py`.
3. `python -m alltoone handoff --check` → `ok`.
4. `python -m alltoone handoff` — пересобрать `.opencode/handoff/pack.md`.
5. Обновить `STATE.md`: что сделано, что дальше, номер версии.
6. `git add -A && git commit -m "chore: release X.Y.Z"`.
7. `git tag vX.Y.Z` — тег локальный; push тегов отдельной командой.

## Отдать наружу (сейчас не сделано)

- **Remote:** `git remote add origin <url>`, затем `git push -u origin main --tags`.
  До этого проекта нет ни у кого, кроме владельца папки.
- **Архив без git:** `git archive --format=zip --prefix=alltoone/ HEAD -o alltoone.zip`.
  Внутри уже всё нужное: CLI, workflow, установщики.
- **PyPI:** `python -m build`, затем `python -m twine upload dist/*`. Нужны `build`
  и `twine`, их в проекте нет намеренно. Перед этим стоит проверить установку из
  архива на чистой машине: `pip install alltoone-*.whl`.
- **Проверка «как у постороннего»:** распаковать архив в пустую папку, запустить
  `scripts/install.ps1` (или `.sh`), затем `install` с `-Target` на тестовый проект
  и пройти цикл `/handoff` → новый чат → `/resume`. Тест установщиков проверяет
  только состав файлов, а не сам прогон.

## Что не публикуется

- `.opencode/handoff/` — состояние конкретной сессии; установщик его не копирует.
- `.opencode/skills/*/SKILL.md` — заработанные навыки с локальными путями и id
  сессий; едет только `skill-template.md`.
