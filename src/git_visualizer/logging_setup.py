"""Настройка локального файлового журнала приложения (ТЗ 12.3).

Модуль находится вне слоёв Domain / Application / Infrastructure / Presentation
и не импортирует Qt. Его вызывает composition root (``main.py``) до создания
главного окна.

Журнал пишется вне рабочей директории репозитория: в ``%LOCALAPPDATA%`` на
Windows или в домашний каталог пользователя. Переменная окружения
``GIT_VISUALIZER_LOG_DIR`` позволяет вручную задать каталог для отладки.

Если каталог или файл создать нельзя, приложение продолжает работу без журнала:
в stderr выводится одно предупреждение, исключение не пробрасывается.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Final

LOG_FILE_NAME: Final = "git-visualizer.log"
LOG_MAX_BYTES: Final = 1_000_000
LOG_BACKUP_COUNT: Final = 3
LOG_FORMAT: Final = "%(asctime)s %(levelname)s %(name)s %(message)s"

LOG_DIR_ENV_VAR: Final = "GIT_VISUALIZER_LOG_DIR"

_HANDLER_MARKER: Final = "_git_visualizer_log_handler"

_warning_lock = threading.Lock()
_warning_emitted = False


def setup_logging(log_dir: Path | None = None, *, level: int = logging.INFO) -> Path:
    """Настраивает файловый журнал и возвращает путь к файлу.

    :param log_dir: каталог журнала; ``None`` — переменная окружения
        ``GIT_VISUALIZER_LOG_DIR`` либо платформенный каталог по умолчанию.
    :param level: минимальный уровень записей журнала.
    :return: путь к файлу журнала (даже если создать его не удалось).

    Повторный вызов не добавляет второй обработчик: существующий файл остаётся
    прежним, обновляется только уровень логирования.
    """
    target = _log_file(log_dir)
    # Корневой логгер: сюда приходят записи всех модулей приложения, включая
    # ``git_visualizer.git`` из git_logging.
    logger = logging.getLogger()

    existing = _existing_handler(logger)
    if existing is not None:
        logger.setLevel(level)
        return Path(existing.baseFilename)

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            target,
            encoding="utf-8",
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
        )
    except Exception as exc:
        _warn_once(
            f"Git Visualizer: cannot create the log file {target}: {exc}. "
            "The application continues without a log."
        )
        return target

    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    setattr(handler, _HANDLER_MARKER, True)
    logger.addHandler(handler)
    logger.setLevel(level)
    return target


def _log_file(log_dir: Path | None) -> Path:
    if log_dir is not None:
        directory = Path(log_dir)
    else:
        override = os.environ.get(LOG_DIR_ENV_VAR)
        directory = Path(override) if override else _default_log_dir()
    return directory / LOG_FILE_NAME


def _default_log_dir() -> Path:
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / "GitVisualizer" / "logs"
    return Path.home() / ".git-visualizer" / "logs"


def _existing_handler(logger: logging.Logger) -> logging.Handler | None:
    for handler in logger.handlers:
        if getattr(handler, _HANDLER_MARKER, False):
            return handler
    return None


def _warn_once(message: str) -> None:
    global _warning_emitted

    with _warning_lock:
        if _warning_emitted:
            return
        _warning_emitted = True

    stream = sys.stderr
    if stream is None:
        return
    try:
        print(message, file=stream)
        stream.flush()
    except Exception:
        # В оконном приложении stderr может отсутствовать.
        pass
