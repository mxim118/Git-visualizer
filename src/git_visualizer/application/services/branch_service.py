"""Сценарии получения веток и меток refs.

Создание и переключение веток (ТЗ, итерация 6) здесь пока не реализованы.
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
