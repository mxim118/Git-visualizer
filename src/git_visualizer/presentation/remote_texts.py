"""Русские тексты для сетевых операций: ход, результат и понятные ошибки.

Нижние слои сообщают об ошибках по-английски; здесь они переводятся в понятные
пользователю сообщения (ТЗ 9.4–9.5, FR-REMOTE-06). Полный вывод Git и команда
остаются в технических подробностях диалога. Приложение не хранит и не запрашивает
пароли и токены: при ошибке авторизации пользователю объясняются штатные механизмы
Git (SSH, Git Credential Manager, credential helpers).
"""

from __future__ import annotations

from git_visualizer.application.errors import (
    InvalidRemoteNameError,
    InvalidRemoteUrlError,
    NoCurrentBranchError,
    RemoteOperationValidationError,
    UnknownRemoteError,
    UpstreamNotConfiguredError,
)
from git_visualizer.application.services.remote_service import (
    RemoteConfigurationContext,
    RemoteConfigurationResult,
    RemoteOperationContext,
    RemoteOperationResult,
)
from git_visualizer.infrastructure.git.git_errors import (
    GitCommandError,
    GitErrorKind,
    GitNotFoundError,
    GitTimeoutError,
)
from git_visualizer.presentation.error_texts import user_message

_OPERATION_TITLES = {
    "fetch": "Fetch",
    "pull": "Pull",
    "push": "Push",
}

_ERROR_TITLES = {
    "fetch": "Не удалось выполнить Fetch",
    "pull": "Не удалось выполнить Pull",
    "push": "Не удалось выполнить Push",
}

_CONFIG_PROGRESS = {
    "add": "Добавление remote",
    "edit": "Изменение remote",
    "remove": "Удаление remote",
}

_CONFIG_ERROR_TITLES = {
    "add": "Не удалось добавить remote",
    "edit": "Не удалось изменить remote",
    "remove": "Не удалось удалить remote",
}

_REMOTE_NAME_RULES_TEXT = (
    "Имя remote не должно быть пустым, содержать пробелы и символы «: ? * [ \\»; "
    "Git проверяет имя до изменения конфигурации."
)
_REMOTE_URL_RULES_TEXT = (
    "Укажите полный URL: HTTPS (https://host/path), SSH (git@host:path или "
    "ssh://git@host/path) либо локальный путь. Схема без хоста (например, "
    "https://) недопустима."
)

_AUTH_TEXT = (
    "Git не смог авторизоваться на remote {remote}. Приложение не хранит и не "
    "запрашивает пароли или токены: используется стандартная авторизация Git — "
    "SSH-ключи, Git Credential Manager или настроенный credential helper. "
    "Проверьте ключ/учётную запись и при необходимости выполните вход через "
    "системный Git, затем повторите операцию. Консольный запрос пароля отключён "
    "(GIT_TERMINAL_PROMPT=0), поэтому Git не зависает в ожидании ввода."
)
_PERMISSION_TEXT = (
    "Remote {remote} отклонил операцию: Git сообщает об отсутствии прав. "
    "Проверьте права на чтение/запись в удалённом репозитории, настройки "
    "защищённой ветки и правила сервера. Приложение не выполняет force push и "
    "не обходит защиту ветки."
)
_NETWORK_TEXT = (
    "Сетевая ошибка при обращении к remote {remote}: не удалось связаться с "
    "сервером (имя хоста, соединение или TLS). Проверьте адрес remote, "
    "подключение к сети и доступность сервера, затем повторите операцию."
)
_REMOTE_NOT_FOUND_TEXT = (
    "Remote {remote} недоступен: Git не нашёл удалённый репозиторий. Проверьте "
    "URL или путь remote (git remote -v) и доступность сервера."
)
_NON_FAST_FORWARD_PULL_TEXT = (
    "Pull отменён: локальная ветка «{branch}» и её upstream {upstream} разошлись, "
    "fast-forward невозможен. Git ничего не изменил. Merge и rebase в приложении "
    "не выполняются — приведите ветки в порядок вручную (например, в терминале) "
    "и повторите Pull."
)
_NON_FAST_FORWARD_PUSH_TEXT = (
    "Push отклонён: удалённая ветка содержит коммиты, которых нет в локальной "
    "(non-fast-forward). Force push не выполняется, поэтому ничего не отправлено. "
    "Сначала выполните Fetch и Pull (только fast-forward), затем повторите Push."
)
_MERGE_CONFLICT_TEXT = (
    "Обнаружен конфликт слияния. Приложение не разрешает конфликты автоматически "
    "и не выполняет merge: завершите слияние вручную в Git, затем повторите "
    "операцию. Технические подробности доступны в разделе «Подробнее»."
)
_NO_CURRENT_BRANCH_TEXT = (
    "Операция невозможна: HEAD не указывает на локальную ветку (detached HEAD "
    "или в репозитории ещё нет коммитов). Переключитесь на локальную ветку и "
    "повторите."
)
_VALIDATION_TEXT = (
    "Операция не запущена: проверьте имя remote и состояние репозитория, затем "
    "повторите. Технические подробности доступны в разделе «Подробнее»."
)


def remote_progress_text(context: RemoteOperationContext) -> str:
    """Короткое сообщение о выполняемой сетевой операции."""
    if context.operation == "fetch":
        remote = f" «{context.remote}»" if context.remote else ""
        return f"Fetch{remote}: получение изменений…"
    if context.operation == "pull":
        branch = f" ветки «{context.branch}»" if context.branch else ""
        return f"Pull{branch} (--ff-only): выполняется…"
    branch = f" «{context.branch}»" if context.branch else ""
    remote = f" в «{context.remote}»" if context.remote else ""
    return f"Push{branch}{remote}: отправка…"


def remote_result_text(result: RemoteOperationResult) -> str:
    """Короткий итог успешной операции для статусной строки."""
    output = result.output.lower()
    if result.operation == "fetch":
        return f"Fetch «{result.remote}»: выполнено."

    if result.operation == "pull":
        base = f"Pull «{result.branch}»"
        if result.upstream:
            base += f" ({result.upstream})"
        if "already up to date" in output:
            return f"{base}: уже актуально."
        if "fast-forward" in output:
            return f"{base}: fast-forward выполнен."
        return f"{base}: выполнено."

    base = f"Push «{result.branch}»"
    if result.remote:
        base += f" в «{result.remote}»"
    elif result.upstream:
        base += f" → {result.upstream}"
    if "everything up-to-date" in output:
        base += ": всё уже отправлено."
    else:
        base += ": выполнено."
    if result.remote and result.upstream:
        base += f" Upstream: {result.upstream}."
    return base


def remote_configuration_progress_text(context: RemoteConfigurationContext) -> str:
    """Короткое сообщение о выполняемой настройке remote."""
    name = context.remote_name or context.new_name
    action = _CONFIG_PROGRESS.get(context.operation, "Операция с remote")
    return f"{action} «{name}»…" if name else f"{action}…"


def remote_configuration_result_text(result: RemoteConfigurationResult) -> str:
    """Короткий итог успешной настройки remote для окна и статусной строки."""
    name = result.remote_name or "remote"
    if result.operation == "add":
        return f"Remote «{name}» добавлен."
    if result.operation == "remove":
        return f"Remote «{name}» удалён."
    if result.new_name and result.new_name != result.remote_name:
        return f"Remote «{name}» переименован в «{result.new_name}»."
    return f"Remote «{name}» изменён."


def remote_configuration_error_title(context: RemoteConfigurationContext) -> str:
    """Заголовок диалога ошибки настройки remote."""
    return _CONFIG_ERROR_TITLES.get(
        context.operation, "Не удалось изменить remote"
    )


def remote_configuration_user_message(
    error: BaseException,
    *,
    context: RemoteConfigurationContext,
) -> str:
    """Понятное пользователю сообщение об отказе настройки remote.

    Для неопознанной ошибки Git в основной текст добавляется начало вывода: Git
    может сообщить собственную причину (ограничения файловой системы, блокировка
    конфигурации). Полный вывод остаётся в разделе «Подробнее».
    """
    name = context.remote_name or context.new_name or "remote"

    if isinstance(error, InvalidRemoteNameError):
        return (
            f"Имя remote «{name}» недопустимо. "
            f"{_REMOTE_NAME_RULES_TEXT}"
        )
    if isinstance(error, InvalidRemoteUrlError):
        return (
            f"URL remote «{name}» недопустим. "
            f"{_REMOTE_URL_RULES_TEXT}"
        )
    if isinstance(error, UnknownRemoteError):
        return (
            f"Remote «{error.remote_name}» не найден в этом репозитории. "
            "Возможно, его удалили; обновите список remote и повторите операцию."
        )
    if isinstance(error, RemoteOperationValidationError):
        return (
            "Операция с remote не выполнена: проверьте имя remote и URL. "
            "Технические подробности доступны в разделе «Подробнее»."
        )

    if isinstance(error, GitCommandError):
        if error.kind is GitErrorKind.REMOTE_ALREADY_EXISTS:
            target = context.new_name or context.remote_name or "remote"
            return (
                f"Remote «{target}» уже существует. Выберите другое имя или "
                "измените существующий remote."
            )
        if error.kind is GitErrorKind.INVALID_REMOTE_NAME:
            return (
                f"Git отклонил имя remote «{name}». "
                f"{_REMOTE_NAME_RULES_TEXT}"
            )
        if error.kind is GitErrorKind.REMOTE_NOT_CONFIGURED:
            return (
                f"Remote «{name}» не найден в локальной конфигурации Git. "
                "Обновите список remote и повторите операцию."
            )
        if error.kind is GitErrorKind.UNKNOWN:
            excerpt = _output_excerpt(error)
            title = _CONFIG_PROGRESS.get(context.operation, "Операция с remote")
            if excerpt:
                return (
                    f"{title} не выполнено (код {error.returncode}). "
                    "Сообщение Git:\n\n"
                    f"{excerpt}\n\n"
                    "Полная команда и вывод — в разделе «Подробнее»."
                )
            return (
                f"{title} не выполнено (код {error.returncode}). Проверьте "
                "локальную конфигурацию Git; технические подробности — в разделе "
                "«Подробнее»."
            )
    return user_message(error)


def remote_error_title(operation: str) -> str:
    """Заголовок диалога ошибки сетевой операции."""
    return _ERROR_TITLES.get(operation, "Не удалось выполнить сетевую операцию")


def remote_user_message(
    error: BaseException,
    *,
    operation: str,
    remote_name: str | None = None,
    remote_url: str | None = None,
    branch_name: str | None = None,
    upstream: str | None = None,
) -> str:
    """Понятное пользователю сообщение об отказе Fetch, Pull или Push.

    ``remote_name``/``remote_url`` описывают выбранный remote, ``branch_name`` и
    ``upstream`` — текущую ветку. Для неопознанной ошибки Git в основной текст
    добавляется начало вывода: удалённый сервер и хуки могут печатать собственную
    причину отказа, не совпадающую со стандартными маркерами Git.
    """
    remote_label = _remote_label(remote_name, remote_url)

    if isinstance(error, UnknownRemoteError):
        return (
            f"Remote «{error.remote_name}» не настроен в этом репозитории. "
            "Проверьте список remote (git remote -v) и повторите операцию."
        )
    if isinstance(error, NoCurrentBranchError):
        return _NO_CURRENT_BRANCH_TEXT
    if isinstance(error, UpstreamNotConfiguredError):
        quoted_branch = f"«{error.branch_name}»"
        if operation == "pull":
            return (
                f"Pull невозможен: у ветки {quoted_branch} не настроен upstream. "
                "Настройте его (например, выполните первый Push с параметром -u) "
                "и повторите Pull."
            )
        return (
            f"Push невозможен: у ветки {quoted_branch} не настроен upstream. "
            "Выполните первый Push с явным выбором remote и параметром -u."
        )
    if isinstance(error, RemoteOperationValidationError):
        return _VALIDATION_TEXT

    if isinstance(error, GitTimeoutError):
        return (
            "Сетевая операция не завершилась за отведённое время "
            f"({error.timeout:g} с). Проверьте подключение и доступность remote "
            f"{remote_label}, затем повторите. Технические подробности доступны "
            "в разделе «Подробнее»."
        )
    if isinstance(error, GitNotFoundError):
        return "Git не найден. Установите Git и перезапустите приложение."

    if isinstance(error, GitCommandError):
        if error.kind is GitErrorKind.REMOTE_NOT_FOUND:
            return _REMOTE_NOT_FOUND_TEXT.format(remote=remote_label)
        if error.kind is GitErrorKind.AUTHENTICATION_FAILED:
            return _AUTH_TEXT.format(remote=remote_label)
        if error.kind is GitErrorKind.PERMISSION_DENIED:
            return _PERMISSION_TEXT.format(remote=remote_label)
        if error.kind is GitErrorKind.NETWORK_ERROR:
            return _NETWORK_TEXT.format(remote=remote_label)
        if error.kind is GitErrorKind.NON_FAST_FORWARD:
            if operation == "pull":
                return _NON_FAST_FORWARD_PULL_TEXT.format(
                    branch=branch_name or "текущая",
                    upstream=upstream or "удалённая ветка",
                )
            return _NON_FAST_FORWARD_PUSH_TEXT
        if error.kind is GitErrorKind.MERGE_CONFLICT:
            return _MERGE_CONFLICT_TEXT
        if error.kind is GitErrorKind.DIRTY_WORKTREE:
            lines = [
                "Операция отменена: незакоммиченные изменения рабочей директории "
                "были бы перезаписаны. Приложение их не удаляет и не перезаписывает."
            ]
            excerpt = _output_excerpt(error)
            if excerpt:
                lines.append(f"Сообщение Git:\n\n{excerpt}")
            lines.append(
                "Закоммитьте изменения или уберите их вручную и повторите операцию."
            )
            return "\n\n".join(lines)
        if error.kind is GitErrorKind.UNMERGED_FILES:
            return (
                "Операция невозможна: в индексе есть неразрешённые конфликты. "
                "Разрешите их в Git и повторите; приложение не разрешает "
                "конфликты автоматически."
            )
        if error.kind is GitErrorKind.OPERATION_IN_PROGRESS:
            return (
                "Операция невозможна: в репозитории есть незавершённый merge, "
                "rebase или cherry-pick. Завершите или прервите его и повторите."
            )
        if error.kind is GitErrorKind.UNKNOWN:
            excerpt = _output_excerpt(error)
            title = _OPERATION_TITLES.get(operation, "Операция")
            if excerpt:
                return (
                    f"{title} не выполнен (код {error.returncode}). "
                    "Сообщение Git:\n\n"
                    f"{excerpt}\n\n"
                    "Полная команда и вывод — в разделе «Подробнее»."
                )
            return (
                f"{title} не выполнен (код {error.returncode}). Проверьте "
                "настройки remote, состояние ветки и рабочей директории; "
                "технические подробности — в разделе «Подробнее»."
            )
    # Прочие виды ошибок Git (блокировка индекса, права на .git, LFS и т. п.)
    # уже имеют понятные русские тексты: не подменяем их сетевым сообщением.
    return user_message(error)


def _remote_label(remote_name: str | None, remote_url: str | None) -> str:
    if remote_name and remote_url:
        return f"«{remote_name}» ({remote_url})"
    if remote_name:
        return f"«{remote_name}»"
    if remote_url:
        return f"«{remote_url}»"
    return "remote"


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
