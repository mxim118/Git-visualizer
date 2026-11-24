"""Сценарий создания коммита из индекса.

Сервис проверяет сообщение и наличие staged changes до запуска Git, а затем просит
Infrastructure выполнить ``git commit -F -``. Сообщение передаётся через stdin: оно
может содержать переводы строк, кавычки и Unicode и не должно попадать в командную
строку. Коммит включает только индекс: ``-a`` и пути не передаются, поэтому
незастейдженные изменения рабочей директории в него не попадают.

Проверка индекса выполняется по актуальному ``git status``, а не по снимку,
загруженному в интерфейс: между обновлением панели и нажатием кнопки файл могли
убрать из индекса снаружи. Ошибки Git (отказ хука, отсутствующий автор, unmerged
файлы) не скрываются: Presentation переводит их в понятный текст и показывает
технические подробности.
"""

from collections.abc import Sequence

from git_visualizer.application.ports import GitRepositoryFactory
from git_visualizer.domain.models import ChangedFile, Repository


class CommitService:
    def __init__(self, git_repository_factory: GitRepositoryFactory) -> None:
        self._git_repository_factory = git_repository_factory

    @staticmethod
    def validate_message(message: str) -> str:
        """Проверяет сообщение коммита; возвращает его без изменений.

        Пустое сообщение и сообщение только из пробельных символов (пробелы,
        табуляции, переводы строк) недопустимы: Git отклонил бы такой коммит.
        Содержательный текст с ведущими или завершающими пробелами не обрезается —
        очистку выполняет Git при создании коммита.

        :raises ValueError: сообщение пустое или состоит только из пробелов.
        """
        if not message.strip():
            raise ValueError("Commit message must not be empty or whitespace-only")
        return message

    @staticmethod
    def has_staged_changes(changes: Sequence[ChangedFile]) -> bool:
        """Есть ли в индексе хотя бы одно изменение для коммита.

        Учитывается только ``staged_change``: незастейдженные и неотслеживаемые
        файлы, а также конфликты не дают права на коммит — Git взял бы в него
        содержимое индекса, а не рабочей директории.
        """
        return any(change.is_staged for change in changes)

    def commit(self, repository: Repository, message: str) -> None:
        """Создаёт коммит из staged changes с сообщением ``message``.

        Порядок проверок: сначала сообщение (дешёвая проверка без Git), затем
        фактическое состояние индекса. Только после этого запускается Git.

        :raises ValueError: сообщение пустое/из пробелов или в индексе нет изменений.
        :raises GitError: команда Git завершилась с ошибкой (пробрасывается без
            изменений; например, отказ Git-хука или отсутствие ``user.name`` /
            ``user.email``).
        """
        message = self.validate_message(message)
        git = self._git_repository_factory(repository.path)
        if not self.has_staged_changes(git.get_status()):
            raise ValueError("There are no staged changes to commit")
        git.commit(message)
