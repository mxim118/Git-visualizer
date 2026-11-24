"""Обёртка над Git CLI для одного репозитория.

Скрывает конкретные команды Git от сервисов. Все команды выполняются через ``GitRunner``
с рабочим каталогом ``path``. Реализованы открытие, история, ветки, remote, статус
рабочей директории, diff рабочей директории, список изменённых файлов коммита и diff
одного файла коммита.

Минимальная версия Git: ``branch --show-current`` требует Git 2.22+.
"""

from pathlib import Path

from git_visualizer.domain.models import (
    Branch,
    ChangedFile,
    Commit,
    Diff,
    FileChange,
    Remote,
)
from git_visualizer.infrastructure.git.git_errors import GitCommandError
from git_visualizer.infrastructure.git.git_parser import (
    BRANCH_FORMAT,
    BRANCH_PATTERNS,
    history_log_format,
    is_binary_diff,
    new_history_separator,
    parse_branches,
    parse_history,
    parse_name_status,
    parse_remotes,
    parse_status,
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


def build_commit_files_arguments(
    *, commit_hash: str, parent_hash: str | None
) -> list[str]:
    """Аргументы списка файлов коммита.

    Обычный и merge-коммит сравниваются с переданным родителем; ``parent_hash=None``
    означает root-коммит и ``--root`` (сравнение с пустым деревом). ``-z`` вместе с
    ``--name-status`` сохраняет пути целиком; ``--find-renames``/``--find-copies``
    позволяют Git сообщить R/C, если он их обнаружил.
    """
    arguments = [
        "diff-tree",
        "--no-commit-id",
        "-r",
        "--name-status",
        "-z",
        "--find-renames",
        # Без harder Git находит копии только среди изменённых файлов; для списка файлов
        # одного коммита это допустимая цена, зато статус copied действительно виден.
        "--find-copies-harder",
    ]
    if parent_hash is None:
        arguments += ["--root", commit_hash]
    else:
        arguments += [parent_hash, commit_hash]
    arguments.append("--")
    return arguments


def build_commit_diff_arguments(
    *,
    commit_hash: str,
    parent_hash: str | None,
    paths: list[str],
) -> list[str]:
    """Аргументы diff одного файла коммита.

    Обычный и merge-коммит: ``git diff <parent> <commit> -- <пути>``. Root-коммит:
    ``git diff-tree --root -p`` (сравнение с пустым деревом). Для переименования
    передаются оба пути, иначе Git не увидит связь между старым и новым файлом и
    покажет новый файл как добавленный. Внешние diff-инструменты и textconv отключены,
    чтобы вывод был предсказуемым.
    """
    if not paths:
        raise ValueError("At least one path is required for a file diff")
    # core.quotePath=false: иначе Git печатает не-ASCII пути в заголовках diff
    # octal-escape-последовательностями, и интерфейс не покажет реальный путь.
    unicode_paths = ["-c", "core.quotePath=false"]
    if parent_hash is None:
        return [
            *unicode_paths,
            "diff-tree",
            "--root",
            "-p",
            "--no-commit-id",
            "-r",
            "--no-ext-diff",
            "--no-color",
            "--no-textconv",
            commit_hash,
            "--",
            *paths,
        ]
    return [
        *unicode_paths,
        "diff",
        "--no-ext-diff",
        "--no-color",
        "--no-textconv",
        parent_hash,
        commit_hash,
        "--",
        *paths,
    ]


def build_status_arguments() -> list[str]:
    """Аргументы статуса рабочей директории (``git --no-optional-locks status``).

    ``--porcelain=v2`` — стабильный формат для разбора: независимые статусы индекса
    и рабочей директории, отдельные типы записей для переименований и unmerged.
    ``-z`` разделяет записи NUL и не экранирует пути. ``--untracked-files=normal``
    (значение по умолчанию) показывает неотслеживаемый каталог одной записью с
    завершающим ``/`` вместо перечисления всех файлов: дешевле на больших проектах.
    ``--branch`` добавляет заголовки ``# branch.*`` — их пропускает парсер.

    ``--no-optional-locks`` (глобальная опция Git, ставится **перед** подкомандой)
    обязательна: по умолчанию ``git status`` обновляет индекс и берёт
    ``.git/index.lock``. Фоновый статус иначе сталкивался бы с ``git add``/``commit``
    пользователя (или с нашими собственными операциями) — второй процесс получает
    «Another git process seems to be running in this repository» и код 128.
    """
    return [
        "--no-optional-locks",
        "status",
        "--porcelain=v2",
        "-z",
        "--branch",
        "--untracked-files=normal",
    ]


def build_worktree_diff_arguments(*, paths: list[str], staged: bool) -> list[str]:
    """Аргументы diff рабочей директории одного файла.

    ``staged=True`` — ``git diff --cached`` (индекс относительно HEAD), иначе
    ``git diff`` (рабочая директория относительно индекса). Для переименования
    передаются оба пути, иначе Git покажет новый файл как добавленный. Внешние
    diff-инструменты и textconv отключены, чтобы вывод был предсказуемым;
    ``core.quotePath=false`` сохраняет не-ASCII пути в заголовках.
    """
    if not paths:
        raise ValueError("At least one path is required for a worktree diff")
    arguments = ["-c", "core.quotePath=false", "diff"]
    if staged:
        arguments.append("--cached")
    arguments += ["--no-ext-diff", "--no-color", "--no-textconv", "--", *paths]
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

    def get_commit_files(
        self, *, commit_hash: str, parent_hash: str | None
    ) -> tuple[FileChange, ...]:
        """Файлы, изменённые коммитом относительно первого родителя.

        ``parent_hash=None`` — root-коммит (сравнение с пустым деревом). Для merge-коммита
        вызывающий передаёт первого родителя. Пустой кортеж — коммит не менял файлов.

        :raises GitError: команда Git завершилась с ошибкой.
        :raises GitOutputParseError: неожиданный формат вывода.
        """
        result = self._run(
            build_commit_files_arguments(
                commit_hash=commit_hash, parent_hash=parent_hash
            )
        )
        return parse_name_status(result.stdout, command=result.command)

    def get_commit_file_diff(
        self,
        *,
        commit_hash: str,
        parent_hash: str | None,
        path: str,
        old_path: str | None = None,
        max_chars: int,
    ) -> Diff:
        """Unified diff одного файла коммита; сравнение с ``parent_hash``.

        Для переименования/копирования передаются оба пути. Для бинарного файла
        ``Diff.is_binary=True`` и ``text`` пуст. Если вывод длиннее ``max_chars``,
        текст обрезается по границе строки и ``is_truncated=True``.

        :raises ValueError: ``max_chars`` меньше 1.
        :raises GitError: команда Git завершилась с ошибкой.
        """
        if max_chars < 1:
            raise ValueError(f"Diff limit must be positive, got {max_chars}")
        paths = [path] if old_path is None else [old_path, path]
        result = self._run(
            build_commit_diff_arguments(
                commit_hash=commit_hash,
                parent_hash=parent_hash,
                paths=paths,
            )
        )

        raw = result.stdout
        if is_binary_diff(raw):
            return Diff(
                path=path,
                text="",
                old_path=old_path,
                is_binary=True,
                base_commit_hash=parent_hash,
            )

        text, truncated = _truncate_diff(raw, max_chars)
        return Diff(
            path=path,
            text=text,
            old_path=old_path,
            is_truncated=truncated,
            base_commit_hash=parent_hash,
        )

    def get_status(self) -> tuple[ChangedFile, ...]:
        """Состояние индекса и рабочей директории (``git status --porcelain=v2 -z``).

        Пустой кортеж — изменений нет. У одной записи могут быть заполнены оба
        изменения: ``MM``/``AM`` означают, что файл изменён ещё раз после ``git add``.

        :raises GitError: команда Git завершилась с ошибкой.
        :raises GitOutputParseError: вывод Git не соответствует формату v2.
        """
        result = self._run(build_status_arguments())
        return parse_status(result.stdout, command=result.command)

    def get_worktree_diff(
        self,
        *,
        path: str,
        old_path: str | None = None,
        staged: bool,
        max_chars: int,
    ) -> Diff:
        """Unified diff одного файла рабочей директории или индекса.

        ``staged=True`` сравнивает индекс с HEAD (``git diff --cached``), иначе —
        рабочую директорию с индексом (``git diff``). ``Diff.base_commit_hash``
        остаётся ``None``: сравнение не привязано к коммиту. Для бинарного файла
        ``is_binary=True`` и пустой ``text``; длинный текст обрезается по границе
        строки с ``is_truncated=True``.

        :raises ValueError: ``max_chars`` меньше 1.
        :raises GitError: команда Git завершилась с ошибкой.
        """
        if max_chars < 1:
            raise ValueError(f"Diff limit must be positive, got {max_chars}")
        paths = [path] if old_path is None else [old_path, path]
        result = self._run(
            build_worktree_diff_arguments(paths=paths, staged=staged)
        )

        raw = result.stdout
        if is_binary_diff(raw):
            return Diff(path=path, text="", old_path=old_path, is_binary=True)

        text, truncated = _truncate_diff(raw, max_chars)
        return Diff(
            path=path, text=text, old_path=old_path, is_truncated=truncated
        )

    def _run(self, args: list[str], *, check: bool = True) -> GitResult:
        return self._runner.run(args, cwd=self._path, check=check)


def _truncate_diff(text: str, max_chars: int) -> tuple[str, bool]:
    """Обрезает слишком длинный diff по границе строки; возвращает текст и признак."""
    if len(text) <= max_chars:
        return text, False
    cut = text[:max_chars]
    newline = cut.rfind("\n")
    if newline >= 0:
        cut = cut[: newline + 1]
    return cut, True
