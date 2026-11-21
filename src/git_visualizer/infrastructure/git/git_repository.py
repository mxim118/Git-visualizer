"""Обёртка над Git CLI для одного репозитория (чтение основных сведений).

Скрывает конкретные команды Git от сервисов. Все команды выполняются через ``GitRunner``
с рабочим каталогом ``path``. Методы получения истории, веток, статуса и diff
добавляются в следующих итерациях.

Минимальная версия Git: ``branch --show-current`` требует Git 2.22+.
"""

from pathlib import Path

from git_visualizer.domain.models import Remote
from git_visualizer.infrastructure.git.git_errors import GitCommandError
from git_visualizer.infrastructure.git.git_parser import parse_remotes
from git_visualizer.infrastructure.git.git_runner import GitResult, GitRunner


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

    def get_remotes(self) -> tuple[Remote, ...]:
        """Настроенные remote; пустой кортеж, если их нет."""
        result = self._run(["remote", "-v"])
        return parse_remotes(result.stdout)

    def _run(self, args: list[str], *, check: bool = True) -> GitResult:
        return self._runner.run(args, cwd=self._path, check=check)
