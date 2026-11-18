# Git Visualizer

Настольный Git-клиент для Windows с интерактивным графом коммитов.
Стек: Python 3.12+, PySide6, системный Git CLI.

> Статус: проект в разработке. Сейчас реализован только каркас: запускается
> пустое окно-заглушка. Git-функциональность появится в следующих итерациях
> (см. раздел 15 технического задания).

## Требования

- Python 3.12 или новее
- Git в `PATH` (понадобится со следующих итераций; сам каркас Git не использует)

## Установка

Windows (PowerShell):

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

Если PowerShell блокирует `Activate.ps1`, разрешите скрипты только для текущей сессии:
`Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned`.

Linux / macOS:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

## Запуск

```bash
python -m git_visualizer.main
```

Либо командой, созданной при установке пакета: `git-visualizer`.

## Тесты

```bash
pytest
```

Тесты используют платформу Qt `offscreen`, поэтому окна при их запуске не открываются.

## Структура

```text
src/git_visualizer/
├── domain/           # модели и правила предметной области (без Qt и subprocess)
├── application/      # сервисы (сценарии использования)
├── infrastructure/   # интеграция с Git CLI
└── presentation/     # окна, виджеты, диалоги, фоновые задачи (PySide6)
tests/
├── unit/
└── integration/
```
