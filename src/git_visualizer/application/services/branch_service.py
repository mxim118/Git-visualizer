"""Сценарии получения, создания и переключения веток и меток refs.

Создание и переключение выполняются через ``git switch``. Перед созданием имя
проверяется по правилам Git (``check-ref-format --branch``); собственная проверка
Application отсекает только заведомо недопустимые значения (пустое имя, дефис в
начале, shorthand ``@{...}``). Принудительных опций нет: если незакоммиченные
изменения конфликтуют с переключением, Git отказывает и репозиторий остаётся
неизменённым. После успешной операции снимок ``Repository`` устаревает (он
описывает HEAD на момент открытия) — Presentation переоткрывает репозиторий.
"""

from collections.abc import Iterable, Mapping

from git_visualizer.application.ports import GitRepositoryFactory
from git_visualizer.domain.enums import RefKind
from git_visualizer.domain.models import Branch, RefLabel, Repository


class BranchService:
    def __init__(self, git_repository_factory: GitRepositoryFactory) -> None:
        self._git_repository_factory = git_repository_factory

    def get_branches(self, repository: Repository) -> tuple[Branch, ...]:
        """Локальные и remote-tracking ветки репозитория.

        Порядок — как у Git (по полному имени, локальные раньше remote-tracking). Если у
        репозитория нет коммитов и текущая ветка ещё не создана (unborn), она добавляется
        первой с ``commit_hash=None`` и ``is_current=True``. При detached HEAD текущей ветки
        нет. Список настроенных remote — ``RepositoryService.get_remotes``, это другое.

        ``repository`` — снимок состояния HEAD: после операций, меняющих HEAD, репозиторий
        нужно открыть заново.

        :raises GitError: ошибка запуска или выполнения Git (пробрасывается без изменений).
        """
        git = self._git_repository_factory(repository.path)
        branches = git.get_branches()
        if repository.is_unborn and repository.current_branch is not None:
            unborn = Branch(
                name=repository.current_branch,
                is_remote=False,
                is_current=True,
                commit_hash=None,
            )
            branches = (unborn, *branches)
        return branches

    def get_ref_labels(self, repository: Repository) -> Mapping[str, tuple[RefLabel, ...]]:
        """Метки для графа: hash коммита → все ссылки, указывающие на него."""
        return build_ref_labels(repository, self.get_branches(repository))

    @staticmethod
    def validate_branch_name(name: str) -> str:
        """Проверяет заведомо недопустимое имя до запуска Git.

        Обрезает окружающие пробельные символы ASCII (пробел, табуляция, переводы
        строк): они не могут входить в имя ветки, а случайно введённые в начале или
        конце не должны мешать. Полная проверка синтаксиса refs остаётся за Git
        (``check-ref-format``); здесь отсекаются случаи, на которых Git отвечает
        ошибкой использования или разрешает неоднозначный shorthand:

        * пустое имя или имя только из пробельных символов;
        * имя, начинающееся с дефиса (его легко спутать с опцией команды);
        * ``@{...}`` — Git трактует это как ссылку на прошлое состояние HEAD, а не
          как имя новой ветки.

        :return: имя без окружающих ASCII-пробелов.
        :raises ValueError: имя пустое или заведомо недопустимо.
        """
        name = name.strip(" \t\r\n")
        if not name:
            raise ValueError("Branch name must not be empty or whitespace-only")
        if name.startswith("-"):
            raise ValueError("Branch name must not start with '-'")
        if "@{" in name:
            raise ValueError("Branch name must not contain '@{'")
        return name

    def create_branch(
        self,
        repository: Repository,
        name: str,
        *,
        start_point: str | None = None,
    ) -> None:
        """Создаёт локальную ветку и переключается на неё.

        ``start_point=None`` — от текущего ``HEAD``; иначе от выбранного коммита
        (передаётся hash). Существующая ветка не перезаписывается, а незакоммиченные
        изменения не удаляются: Git либо переносит их безопасно, либо отказывает,
        оставляя рабочую директорию и refs как были.

        :raises ValueError: пустое или заведомо недопустимое имя.
        :raises GitError: Git отклонил имя, ветка уже существует, коммит не найден
            или переключение небезопасно (пробрасывается без изменений).
        """
        name = self.validate_branch_name(name)
        if start_point is not None:
            start_point = start_point.strip()
            if not start_point:
                raise ValueError("Branch start point must not be empty")

        git = self._git_repository_factory(repository.path)
        git.validate_branch_name(name)
        git.create_branch(name, start_point=start_point)

    def switch_branch(self, repository: Repository, name: str) -> None:
        """Переключается на существующую локальную ветку через ``git switch``.

        Безопасность рабочей директории обеспечивает Git: приложение не передаёт
        опций принудительного переключения и не удаляет изменения. Если Git отказывает,
        причина приходит типизированной ошибкой, а репозиторий остаётся неизменным.

        :raises ValueError: пустое или заведомо недопустимое имя.
        :raises GitError: ветка не найдена, переключение перезаписало бы изменения
            или идёт незавершённая операция (пробрасывается без изменений).
        """
        name = self.validate_branch_name(name)
        git = self._git_repository_factory(repository.path)
        git.switch_branch(name)


def build_ref_labels(
    repository: Repository, branches: Iterable[Branch]
) -> dict[str, tuple[RefLabel, ...]]:
    """Сопоставляет ссылки с коммитами для меток графа.

    Ключ — полный hash коммита; значение — метки всех ссылок на него, ни одна не теряется.
    Порядок меток детерминирован: ``HEAD`` при detached HEAD, затем локальные ветки (текущая
    первой, остальные по имени), затем remote-tracking ветки по имени. У ветки без коммита
    (unborn) метки нет — ей некуда крепиться. Ключи — только коммиты, на которые что-то
    указывает; коммитов без ссылок в словаре нет.

    При обычном (не detached) HEAD отдельной метки ``HEAD`` нет: текущая ветка помечена
    флагом ``is_current``.
    """
    grouped: dict[str, list[RefLabel]] = {}

    if repository.is_detached and repository.head_commit is not None:
        grouped.setdefault(repository.head_commit, []).append(
            RefLabel(name="HEAD", full_name="HEAD", kind=RefKind.DETACHED_HEAD, is_current=True)
        )

    ordered = sorted(branches, key=lambda b: (b.is_remote, not b.is_current, b.name))
    for branch in ordered:
        if branch.commit_hash is None:
            continue
        label = RefLabel(
            name=branch.name,
            full_name=branch.ref_name,
            kind=RefKind.REMOTE_BRANCH if branch.is_remote else RefKind.LOCAL_BRANCH,
            is_current=branch.is_current,
        )
        grouped.setdefault(branch.commit_hash, []).append(label)

    return {commit_hash: tuple(labels) for commit_hash, labels in grouped.items()}
