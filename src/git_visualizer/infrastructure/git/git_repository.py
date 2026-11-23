"""Обёртка над Git CLI для одного репозитория (чтение основных сведений).

Скрывает конкретные команды Git от сервисов. Все команды выполняются через ``GitRunner``
с рабочим каталогом ``path``. Методы получения истории, веток, статуса и diff
добавляются в следующих итерациях.

Минимальная версия Git: ``branch --show-current`` требует Git 2.22+.
"""

from pathlib import Path

from git_visualizer.domain.models import Branch, Commit, Remote
from git_visualizer.infrastructure.git.git_errors import GitCommandError
from git_visualizer.infrastructure.git.git_parser import (
    BRANCH_FORMAT,
    BRANCH_PATTERNS,
    history_log_format,
    new_history_separator,
    parse_branches,
    parse_history,
    parse_remotes,
)
from git_visualizer.infrastructure.git.git_runner import GitResult, GitRunner


def build_history_arguments(*, include_head: bool, limit: int, separator: str) -> list[str]:
    """Аргументы ``git log`` для получения истории (ТЗ 5.2, FR-HIST-01).

    * ``--branches --remotes`` — локальные и remote-tracking ветки. ``--all`` не
      используется: он добавляет ``refs/stash`` и служебные коммиты stash в граф.
    * ``HEAD`` добавляется только если он указывает на коммит (``include_head``): без
      него пропали бы коммиты detached HEAD, а в репозитории без коммитов Git с ``HEAD``
      завершается ошибкой.
    * ``--topo-order`` — потомки всегда выводятся раньше своих родителей, даже если даты
      коммитов «перепутаны»; порядок по дате граф бы ломал (ТЗ FR-HIST-03).
    * ``--decorate-refs`` — явный список декорируемых refs (``HEAD``, ветки, remote-tracking
      ветки): настройка пользователя ``log.excludeDecoration`` иначе молча скрывала бы
      ветки в ``%D``; теги и ``refs/stash`` отсекаются тут же. ``HEAD`` нужно перечислять
      явно, иначе пропадает маркер ``HEAD ->``. Опция есть в Git 2.22+.
    * ``--encoding=UTF-8`` — вывод в UTF-8, даже если у пользователя настроен
      ``i18n.logOutputEncoding``; ``--no-show-signature`` и ``--no-color`` — вывод Git не
      засоряется настройками пользователя; ``-z`` — записи разделены NUL.
    * Завершающий ``--``: без него файл с именем ``HEAD`` в рабочей директории делает
      ``git log HEAD`` неоднозначным и приводит к ошибке.
    """
    if limit < 1:
        raise ValueError(f"History limit must be positive, got {limit}")
    arguments = ["log", "--branches", "--remotes"]
    if include_head:
        arguments.append("HEAD")
    arguments += [
        "--topo-order",
        f"--max-count={limit}",
        "--decorate=full",
        "--decorate-refs=HEAD",
        "--decorate-refs=refs/heads/",
        "--decorate-refs=refs/remotes/",
        "--encoding=UTF-8",
        "--no-color",
        "--no-show-signature",
        "-z",
        f"--format={history_log_format(separator)}",
        "--",
    ]
    return arguments


class GitRepository:
    """Запросы к репозиторию, расположенному в каталоге ``path`` или внутри него."""

    def __init__(self, runner: GitRunner, path: Path) -> None:
        self._runner = runner
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def is_inside_work_tree(self) -> bool:
        """``True``, если ``path`` находится внутри рабочей директории репозитория.

        ``False`` для bare-репозитория и для каталога ``.git`` обычного репозитория.

        :raises GitCommandError: ``path`` вообще не относится к репозиторию
            (``error.kind`` — ``NOT_A_REPOSITORY``) либо другая ошибка Git.
        """
        result = self._run(["rev-parse", "--is-inside-work-tree"])
        return result.stdout.strip() == "true"

    def is_bare_repository(self) -> bool:
        result = self._run(["rev-parse", "--is-bare-repository"])
        return result.stdout.strip() == "true"

    def get_toplevel(self) -> Path:
        """Корень рабочей директории (символические ссылки Git разворачивает сам).

        :raises GitCommandError: ``path`` не внутри рабочей директории (например, bare).
        """
        result = self._run(["rev-parse", "--show-toplevel"])
        return Path(result.stdout.removesuffix("\n"))

    def get_current_branch(self) -> str | None:
        """Имя текущей ветки; ``None`` при detached HEAD.

        В репозитории без коммитов возвращает имя будущей (unborn) ветки.
        """
        result = self._run(["branch", "--show-current"])
        name = result.stdout.removesuffix("\n")
        return name or None

    def get_head_commit(self) -> str | None:
        """Полный hash коммита, на который указывает HEAD; ``None``, если коммитов ещё нет."""
        # Код 1 при -q означает «HEAD не разрешается в коммит» (unborn-ветка) — штатный ответ.
        result = self._run(["rev-parse", "--verify", "-q", "HEAD"], check=False)
        if result.returncode == 0:
            return result.stdout.strip()
        if result.returncode == 1:
            return None
        raise GitCommandError(
            result.returncode,
            command=result.command,
            stdout=result.stdout,
            stderr=result.stderr,
        )

    def has_head(self) -> bool:
        return self.get_head_commit() is not None

    def get_history(self, *, limit: int) -> tuple[Commit, ...]:
        """История коммитов всех локальных и remote-tracking веток и ``HEAD``.

        Коммиты идут в топологическом порядке (потомок раньше родителей), новые — первыми.
        Не более ``limit`` коммитов; значение по умолчанию намеренно не задаётся здесь —
        оно определено в одном месте (``application``). Если ``limit`` обрезал историю,
        родители последних коммитов в результате отсутствуют: это обычная ситуация для
        потребителя (граф). Пустой кортеж — коммитов нет.

        Запускает два процесса Git: проверку ``HEAD`` и ``git log``.

        :raises ValueError: ``limit`` меньше 1.
        :raises GitCommandError: команда Git завершилась с ошибкой.
        :raises GitOutputParseError: вывод Git не соответствует ожидаемому формату.
        """
        separator = new_history_separator()
        arguments = build_history_arguments(
            include_head=self.has_head(), limit=limit, separator=separator
        )
        result = self._run(arguments)
        return parse_history(result.stdout, separator=separator, command=result.command)

    def get_branches(self) -> tuple[Branch, ...]:
        """Локальные ветки и remote-tracking ветки (``git for-each-ref``, один процесс Git).

        Порядок — как у Git: по полному имени, сначала локальные. Для каждой ветки заполнены
        имя, тип, hash коммита, признак текущей (только у локальной ветки, на которую указывает
        HEAD) и upstream (только у локальных). Symbolic refs (``origin/HEAD``) — не ветки и не
        возвращаются. При detached HEAD текущей нет ни у одной ветки. Unborn-ветка (HEAD в
        репозитории без коммитов) в выводе Git отсутствует — её добавляет ``BranchService``.
        Список настроенных remote — отдельный запрос ``get_remotes``.

        :raises GitError: команда Git завершилась с ошибкой.
        :raises GitOutputParseError: вывод Git не соответствует ожидаемому формату.
        """
        result = self._run(["for-each-ref", f"--format={BRANCH_FORMAT}", *BRANCH_PATTERNS])
        return parse_branches(result.stdout, command=result.command)

    def get_remotes(self) -> tuple[Remote, ...]:
        """Настроенные remote; пустой кортеж, если их нет."""
        result = self._run(["remote", "-v"])
        return parse_remotes(result.stdout)

    def _run(self, args: list[str], *, check: bool = True) -> GitResult:
        return self._runner.run(args, cwd=self._path, check=check)
