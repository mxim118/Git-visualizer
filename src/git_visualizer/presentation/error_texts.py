"""Русские тексты для ошибок Application и Git.

Нижние слои сообщают об ошибках по-английски; перевод и понятные пользователю
подсказки собраны в одном месте Presentation (ТЗ 9.4, 9.5). Технические подробности
показываются отдельно — в разделе «Подробнее» диалога.
"""

from __future__ import annotations

from git_visualizer.application.errors import (
    BareRepositoryNotSupportedError,
    NotAGitRepositoryError,
    PathDoesNotExistError,
    PathIsNotDirectoryError,
    RepositoryOpenError,
    UnsafeRepositoryError,
)
from git_visualizer.infrastructure.git.git_errors import (
    GitCommandError,
    GitError,
    GitErrorKind,
    GitLaunchError,
    GitNotFoundError,
    GitOutputParseError,
    GitTimeoutError,
)

GIT_INSTALL_URL = "https://git-scm.com/downloads"

_GIT_NOT_FOUND_TEXT = (
    "Git не найден. Установите Git и перезапустите приложение: "
    f"{GIT_INSTALL_URL}. При установке включите добавление Git в PATH."
)
_GIT_LAUNCH_TEXT = (
    "Не удалось запустить Git. Проверьте, что Git установлен правильно и доступен "
    "для запуска."
)
_GIT_PARSE_TEXT = (
    "Не удалось разобрать ответ Git. Возможно, установлена несовместимая версия Git "
    "или репозиторий повреждён."
)
_UNEXPECTED_TEXT = (
    "Не удалось выполнить операцию. Попробуйте ещё раз; технические подробности "
    "доступны в разделе «Подробнее»."
)
_MISSING_AUTHOR_TEXT = (
    "Git не знает, кто автор коммита: не настроены user.name и user.email. "
    "Настройте их и повторите коммит:\n"
    'git config --global user.name "Ваше имя"\n'
    'git config --global user.email "you@example.com"'
)
_UNMERGED_FILES_TEXT = (
    "Коммит невозможен: в индексе есть неразрешённые конфликты. Разрешите их в Git "
    "(например, через git add) и повторите коммит; технические подробности доступны "
    "в разделе «Подробнее»."
)
_COMMIT_VALIDATION_TEXT = (
    "Коммит не создан: проверьте сообщение (оно не должно быть пустым или состоять "
    "из пробелов) и наличие staged-изменений в индексе."
)
_LFS_FILTER_TEXT = (
    "Git LFS не смог обработать файл, поэтому Git не смог определить состояние "
    "репозитория. Обычно это означает, что Git LFS не может записать временный файл "
    "в каталог «.git/lfs/tmp»: у текущего пользователя нет прав на этот каталог "
    "либо его блокирует другая программа. Проверьте права на «.git/lfs/tmp» и "
    "повторите операцию; технические подробности доступны в разделе «Подробнее»."
)
_REPOSITORY_NOT_WRITABLE_TEXT = (
    "Git не может изменить репозиторий: нет доступа на запись в каталог «.git». "
    "Git не смог создать служебный файл «index.lock» (Permission denied). "
    "Проверьте, что репозиторий принадлежит текущему пользователю, каталог не "
    "доступен только для чтения и файлы не блокирует другая программа; технические "
    "подробности доступны в разделе «Подробнее»."
)
_INDEX_LOCKED_TEXT = (
    "Git уже выполняет другую операцию с этим репозиторием: файл "
    "«.git/index.lock» занят. Дождитесь завершения другого Git-процесса "
    "(например, коммита, merge или редактора) и повторите. Если Git завершился "
    "аварийно, файл блокировки может остаться — удалите его вручную; приложение "
    "не делает это автоматически. Технические подробности доступны в разделе "
    "«Подробнее»."
)


def user_message(error: BaseException) -> str:
    """Понятное пользователю русское сообщение для известной ошибки."""
    if isinstance(error, PathDoesNotExistError):
        return (
            f"Папка не найдена: «{error.path}». Проверьте путь и выберите "
            "существующую папку."
        )
    if isinstance(error, PathIsNotDirectoryError):
        return (
            f"Выбранный путь не является папкой: «{error.path}». Выберите папку "
            "с Git-репозиторием."
        )
    if isinstance(error, NotAGitRepositoryError):
        return (
            f"В выбранной папке нет Git-репозитория: «{error.path}». Выберите папку "
            "проекта с уже созданным репозиторием (можно выбрать и вложенную папку)."
        )
    if isinstance(error, BareRepositoryNotSupportedError):
        return (
            f"Bare-репозитории не поддерживаются: «{error.path}». Выберите рабочую "
            "папку проекта с обычным репозиторием."
        )
    if isinstance(error, UnsafeRepositoryError):
        return unsafe_repository_message(error)
    if isinstance(error, RepositoryOpenError):
        return f"Не удалось открыть репозиторий: «{error.path}»."

    if isinstance(error, GitNotFoundError):
        return _GIT_NOT_FOUND_TEXT
    if isinstance(error, GitTimeoutError):
        return (
            "Git не ответил за отведённое время. Проверьте, что репозиторий "
            "доступен, и повторите операцию."
        )
    if isinstance(error, GitLaunchError):
        return _GIT_LAUNCH_TEXT
    if isinstance(error, GitOutputParseError):
        return _GIT_PARSE_TEXT
    if isinstance(error, GitCommandError):
        if error.kind is GitErrorKind.NOT_A_REPOSITORY:
            return "Папка больше не является Git-репозиторием."
        if error.kind is GitErrorKind.UNSAFE_REPOSITORY:
            return (
                "Git отказывается работать с этой папкой из-за владельца. "
                "Если репозиторий ваш, разрешите его в Git (см. «Подробнее»)."
            )
        if error.kind is GitErrorKind.MISSING_AUTHOR:
            return _MISSING_AUTHOR_TEXT
        if error.kind is GitErrorKind.UNMERGED_FILES:
            return _UNMERGED_FILES_TEXT
        if error.kind is GitErrorKind.LFS_FILTER_FAILED:
            return _LFS_FILTER_TEXT
        if error.kind is GitErrorKind.REPOSITORY_NOT_WRITABLE:
            return _REPOSITORY_NOT_WRITABLE_TEXT
        if error.kind is GitErrorKind.INDEX_LOCKED:
            return _INDEX_LOCKED_TEXT
        return (
            f"Git завершился с ошибкой (код {error.returncode}). "
            "Технические подробности доступны в разделе «Подробнее»."
        )
    if isinstance(error, GitError):
        return "Не удалось выполнить операцию Git."
    return _UNEXPECTED_TEXT


def commit_user_message(error: BaseException) -> str:
    """Понятное сообщение для отказа Git именно при создании коммита (FR-COMMIT-06).

    Для неопознанной ошибки Git в основной текст добавляется начало вывода команды:
    Git-хук может печатать собственную причину отказа, не добавляя стандартного
    маркера, поэтому без этого пользователь увидел бы её только в «Подробнее».
    Полные stdout/stderr и команда по-прежнему доступны в ``technical_details``.
    """
    if isinstance(error, ValueError):
        return _COMMIT_VALIDATION_TEXT
    if isinstance(error, GitCommandError):
        if error.kind is GitErrorKind.UNKNOWN:
            excerpt = _output_excerpt(error)
            if excerpt:
                return (
                    f"Git не создал коммит (код {error.returncode}). "
                    "Сообщение Git:\n\n"
                    f"{excerpt}\n\n"
                    "Полная команда и вывод — в разделе «Подробнее»."
                )
            return (
                f"Git не создал коммит (код {error.returncode}). Проверьте настройки "
                "автора коммита, Git-хуки и состояние индекса. Технические "
                "подробности доступны в разделе «Подробнее»."
            )
    return user_message(error)


_BRANCH_NAME_RULES_TEXT = (
    "В имени ветки нельзя использовать пробелы и символы ~ ^ : ? * [ \\, "
    "последовательность «..» и «@{», имя не может начинаться с «-», "
    "заканчиваться точкой или «.lock»."
)


def branch_user_message(
    error: BaseException,
    *,
    operation: str,
    branch_name: str | None = None,
    start_point: str | None = None,
) -> str:
    """Понятное сообщение для отказа при создании или переключении ветки.

    ``operation`` — ``"create"`` или ``"switch"``. Для грязной рабочей директории
    (``DIRTY_WORKTREE``) в основной текст добавляется начало вывода Git: там Git
    перечисляет файлы, из-за которых переключение было бы небезопасным. Полные
    stdout/stderr и команда остаются в ``technical_details``.
    """
    quoted = f"«{branch_name}»" if branch_name else "ветки"
    is_create = operation == "create"
    action = "создать ветку" if is_create else "переключить ветку"
    failed_title = "Ветка не создана" if is_create else "Переключение отменено"

    if isinstance(error, ValueError):
        if is_create:
            return (
                "Ветка не создана: имя недопустимо. Оно не должно быть пустым или "
                "состоять из пробелов, начинаться с «-» или содержать «@{». "
                f"{_BRANCH_NAME_RULES_TEXT}"
            )
        return (
            "Переключение отменено: имя ветки не указано или недопустимо. "
            "Выберите ветку в списке ещё раз."
        )

    if isinstance(error, GitCommandError):
        if error.kind is GitErrorKind.INVALID_BRANCH_NAME:
            return (
                f"{failed_title}: Git отклонил имя {quoted}. "
                f"{_BRANCH_NAME_RULES_TEXT}"
            )
        if error.kind is GitErrorKind.BRANCH_ALREADY_EXISTS:
            return (
                f"Ветка {quoted} уже существует. Выберите другое имя или "
                "переключитесь на неё в списке веток."
            )
        if error.kind is GitErrorKind.REF_NOT_FOUND:
            if is_create and start_point:
                short = start_point[:12]
                return (
                    f"Ветка не создана: коммит «{short}» не найден в репозитории. "
                    "Обновите историю и выберите коммит заново."
                )
            return (
                f"Переключение отменено: ветка {quoted} не найдена. Возможно, её "
                "удалили; обновите список веток и повторите."
            )
        if error.kind is GitErrorKind.DIRTY_WORKTREE:
            lines = [
                f"{failed_title}: Git не может {action}, потому что незакоммиченные "
                "изменения в рабочей директории были бы перезаписаны. Изменения "
                "не тронуты."
            ]
            excerpt = _output_excerpt(error)
            if excerpt:
                lines.append(f"Сообщение Git:\n\n{excerpt}")
            lines.append(
                "Закоммитьте изменения или уберите их вручную и повторите операцию."
            )
            return "\n\n".join(lines)
        if error.kind is GitErrorKind.OPERATION_IN_PROGRESS:
            return (
                f"{failed_title}: в репозитории есть незавершённая операция "
                "(merge, rebase или cherry-pick). Завершите или прервите её, "
                "затем повторите."
            )
        if error.kind is GitErrorKind.UNMERGED_FILES:
            return (
                f"{failed_title}: в индексе есть неразрешённые конфликты. "
                "Разрешите их (например, через git add) и повторите операцию."
            )
        if error.kind is GitErrorKind.UNKNOWN:
            excerpt = _output_excerpt(error)
            if excerpt:
                return (
                    f"Git не смог {action} (код {error.returncode}). "
                    "Сообщение Git:\n\n"
                    f"{excerpt}\n\n"
                    "Полная команда и вывод — в разделе «Подробнее»."
                )
            return (
                f"Git не смог {action} (код {error.returncode}). Проверьте список "
                "веток и состояние рабочей директории; технические подробности — "
                "в разделе «Подробнее»."
            )
    return user_message(error)


def _output_excerpt(error: GitCommandError, *, max_lines: int = 8) -> str:
    """Первые непустые строки stderr и stdout для основного текста ошибки."""
    lines: list[str] = []
    for stream in (error.stderr, error.stdout):
        for raw_line in stream.splitlines():
            line = raw_line.rstrip()
            if not line.strip():
                continue
            lines.append(line if len(line) <= 500 else f"{line[:500]}…")
            if len(lines) >= max_lines:
                return "\n".join(lines)
    return "\n".join(lines)



def unsafe_repository_message(error: UnsafeRepositoryError) -> str:
    """Сообщение с командой, разрешающей каталог с «чужим» владельцем."""
    return (
        "Git отказывается работать с этой папкой, потому что её владелец "
        "не совпадает с текущим пользователем. Если репозиторий ваш, выполните "
        "команду и попробуйте снова:\n"
        f'git config --global --add safe.directory "{error.path}"'
    )


def technical_details(error: BaseException) -> str:
    """Многострочные технические подробности для раздела «Подробнее»."""
    details = getattr(error, "technical_details", None)
    if callable(details):
        try:
            text = str(details())
        except Exception:  # noqa: BLE001 - форматирование не должно скрывать ошибку
            text = ""
        if text:
            return text
    return f"{type(error).__name__}: {error}"
