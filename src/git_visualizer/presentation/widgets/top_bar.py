"""Верхняя панель основного окна: репозиторий, ветка, remote и основные действия."""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

from git_visualizer.domain.models import Remote, Repository

_PLACEHOLDER = "—"
_OPERATION_COLOR = "#2563eb"
_RESULT_COLOR = "#15803d"
_ERROR_COLOR = "#b91c1c"


class TopBar(QFrame):
    """Панель с названием приложения, именем репозитория, веткой и кнопками.

    Remote выбирается в списке: Fetch и первый Push (``-u``) выполняются для
    выбранного remote. Pull и обычный Push работают по настроенному upstream
    текущей ветки. Виджет не запускает Git и не принимает решений: кнопки только
    испускают сигналы, а допустимость операции окончательно проверяет Application.
    """

    refresh_requested = Signal()
    close_repository_requested = Signal()
    fetch_requested = Signal()
    pull_requested = Signal()
    push_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("top_bar")
        self.setFrameShape(QFrame.Shape.StyledPanel)

        self._has_repository = False
        self._busy = False
        self._repository: Repository | None = None
        self._remote_names: tuple[str, ...] = ()

        self.app_title_label = QLabel("Git Visualizer")
        self.app_title_label.setObjectName("app_title_label")
        self.app_title_label.setStyleSheet("font-size: 16px; font-weight: 600;")

        self.repository_name_label = QLabel(_PLACEHOLDER)
        self.repository_name_label.setObjectName("repository_name_label")
        self.repository_name_label.setStyleSheet("font-weight: 600;")

        self.branch_label = QLabel("Ветка: —")
        self.branch_label.setObjectName("current_branch_label")
        self.branch_label.setStyleSheet("color: #334155;")

        self.remote_label = QLabel("Remote:")
        self.remote_label.setObjectName("remote_choice_label")
        self.remote_label.setStyleSheet("color: #334155;")

        self.remote_combo = QComboBox(self)
        self.remote_combo.setObjectName("remote_combo")
        self.remote_combo.setToolTip("Remote для Fetch и первого Push")
        self.remote_combo.setMinimumContentsLength(10)
        self.remote_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        self.remote_combo.currentIndexChanged.connect(self._update_action_states)

        # Итог последней сетевой операции остаётся видимым после перезагрузки данных;
        # «Загрузка…» показывается отдельной меткой, чтобы результат не терялся.
        self.remote_status_label = QLabel(self)
        self.remote_status_label.setObjectName("remote_operation_status")
        self.remote_status_label.setStyleSheet(f"color: {_OPERATION_COLOR};")
        self.remote_status_label.hide()

        self.status_label = QLabel()
        self.status_label.setObjectName("operation_status_label")
        self.status_label.setStyleSheet("color: #2563eb;")
        self.status_label.hide()

        self.fetch_button = self._make_action_button("Fetch", "fetch_button")
        self.pull_button = self._make_action_button("Pull", "pull_button")
        self.push_button = self._make_action_button("Push", "push_button")

        self.refresh_button = self._make_action_button("Обновить", "refresh_button")
        self.refresh_button.setToolTip("Обновить историю репозитория")
        self.refresh_button.clicked.connect(self.refresh_requested)

        self.close_button = self._make_action_button(
            "Закрыть", "close_repository_button"
        )
        self.close_button.setToolTip("Закрыть репозиторий и вернуться на стартовый экран")
        self.close_button.clicked.connect(self.close_repository_requested)

        self.fetch_button.clicked.connect(self.fetch_requested)
        self.pull_button.clicked.connect(self.pull_requested)
        self.push_button.clicked.connect(self.push_requested)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(12)
        layout.addWidget(self.app_title_label)
        layout.addWidget(self.repository_name_label)
        layout.addWidget(self.branch_label)
        layout.addWidget(self.remote_label)
        layout.addWidget(self.remote_combo)
        layout.addStretch(1)
        layout.addWidget(self.remote_status_label)
        layout.addWidget(self.status_label)
        layout.addWidget(self.fetch_button)
        layout.addWidget(self.pull_button)
        layout.addWidget(self.push_button)
        layout.addWidget(self.refresh_button)
        layout.addWidget(self.close_button)

        self._update_action_states()

    def set_repository(self, repository: Repository) -> None:
        """Показывает имя репозитория (путь — в подсказке) и состояние HEAD."""
        self._has_repository = True
        self._repository = repository
        self.repository_name_label.setText(repository.name)
        self.repository_name_label.setToolTip(str(repository.path))
        self.branch_label.setText(f"Ветка: {_branch_text(repository)}")
        self.branch_label.setToolTip(_branch_tooltip(repository))
        self.close_button.setEnabled(True)
        self._update_action_states()

    def set_remotes(self, remotes: Iterable[Remote]) -> None:
        """Заполняет список настроенных remote, сохраняя прежний выбор, если он есть."""
        remotes = tuple(remotes)
        names = tuple(remote.name for remote in remotes)
        selected = self.selected_remote

        self.remote_combo.blockSignals(True)
        self.remote_combo.clear()
        for remote in remotes:
            self.remote_combo.addItem(remote.name)
            lines = [remote.name]
            if remote.fetch_url:
                lines.append(f"Fetch: {remote.fetch_url}")
            if remote.push_url:
                lines.append(f"Push: {remote.push_url}")
            self.remote_combo.setItemData(
                self.remote_combo.count() - 1,
                "\n".join(lines),
                Qt.ItemDataRole.ToolTipRole,
            )
        if selected in names:
            self.remote_combo.setCurrentIndex(names.index(selected))
        elif "origin" in names:
            self.remote_combo.setCurrentIndex(names.index("origin"))
        elif names:
            self.remote_combo.setCurrentIndex(0)
        self.remote_combo.blockSignals(False)

        self._remote_names = names
        self.remote_combo.setToolTip(
            "Remote для Fetch и первого Push"
            if names
            else "В репозитории не настроен ни один remote"
        )
        self._update_action_states()

    def clear(self) -> None:
        self._has_repository = False
        self._repository = None
        self._remote_names = ()
        self.repository_name_label.setText(_PLACEHOLDER)
        self.repository_name_label.setToolTip("")
        self.branch_label.setText("Ветка: —")
        self.branch_label.setToolTip("")
        self.remote_combo.blockSignals(True)
        self.remote_combo.clear()
        self.remote_combo.blockSignals(False)
        self.clear_remote_status()
        self.close_button.setEnabled(False)
        self.set_busy(False)

    def set_busy(self, busy: bool, message: str | None = None) -> None:
        """Показывает индикатор выполнения и блокирует повторные действия."""
        self._busy = busy
        if busy:
            self.status_label.setText(message or "Загрузка…")
        self.status_label.setVisible(busy)
        self._update_action_states()

    def show_remote_progress(self, text: str) -> None:
        """Показывает ход сетевой операции, не скрывая итог предыдущей."""
        self.remote_status_label.setText(text)
        self.remote_status_label.setStyleSheet(f"color: {_OPERATION_COLOR};")
        self.remote_status_label.setToolTip(text)
        self.remote_status_label.show()

    def show_remote_result(
        self, text: str, *, details: str = "", error: bool = False
    ) -> None:
        """Показывает итог сетевой операции; полный вывод Git — в подсказке."""
        self.remote_status_label.setText(text)
        self.remote_status_label.setStyleSheet(
            f"color: {_ERROR_COLOR if error else _RESULT_COLOR};"
        )
        self.remote_status_label.setToolTip(details or text)
        self.remote_status_label.show()

    def clear_remote_status(self) -> None:
        self.remote_status_label.clear()
        self.remote_status_label.setToolTip("")
        self.remote_status_label.hide()

    @property
    def is_busy(self) -> bool:
        return self._busy

    @property
    def has_repository(self) -> bool:
        return self._has_repository

    @property
    def remote_names(self) -> tuple[str, ...]:
        """Имена настроенных remote в порядке Git."""
        return self._remote_names

    @property
    def selected_remote(self) -> str | None:
        """Имя выбранного remote или ``None``, если remote не настроены."""
        name = self.remote_combo.currentText().strip()
        return name or None

    def _update_action_states(self) -> None:
        ready = self._has_repository and not self._busy
        has_remotes = bool(self._remote_names)
        repository = self._repository
        branch_ready = (
            ready
            and has_remotes
            and repository is not None
            and repository.current_branch is not None
            and not repository.is_unborn
        )

        self.refresh_button.setEnabled(ready)
        self.remote_combo.setEnabled(ready and has_remotes)
        self.fetch_button.setEnabled(ready and has_remotes)
        self.pull_button.setEnabled(branch_ready)
        self.push_button.setEnabled(branch_ready)
        self.fetch_button.setToolTip(self._fetch_tooltip(ready, has_remotes))
        self.pull_button.setToolTip(self._branch_tooltip(ready, has_remotes))
        self.push_button.setToolTip(self._push_tooltip(ready, has_remotes))

    def _fetch_tooltip(self, ready: bool, has_remotes: bool) -> str:
        if self._busy:
            return "Выполняется операция Git…"
        if not self._has_repository:
            return "Репозиторий ещё не открыт."
        if not has_remotes:
            return "В репозитории не настроен ни один remote."
        remote = self.selected_remote or "remote"
        return f"Получить изменения из «{remote}» (git fetch)."

    def _branch_tooltip(self, ready: bool, has_remotes: bool) -> str:
        if self._busy:
            return "Выполняется операция Git…"
        if not self._has_repository:
            return "Репозиторий ещё не открыт."
        if not has_remotes:
            return "В репозитории не настроен ни один remote."
        repository = self._repository
        if repository is not None and repository.is_unborn:
            return "В репозитории ещё нет коммитов."
        if repository is not None and repository.current_branch is None:
            return "HEAD не указывает на локальную ветку (detached HEAD)."
        return (
            "Получить изменения текущей ветки только fast-forward "
            "(--ff-only); при расхождении веток ничего не изменится."
        )

    def _push_tooltip(self, ready: bool, has_remotes: bool) -> str:
        if self._busy:
            return "Выполняется операция Git…"
        if not self._has_repository:
            return "Репозиторий ещё не открыт."
        if not has_remotes:
            return "В репозитории не настроен ни один remote."
        repository = self._repository
        if repository is not None and repository.is_unborn:
            return "В репозитории ещё нет коммитов."
        if repository is not None and repository.current_branch is None:
            return "HEAD не указывает на локальную ветку (detached HEAD)."
        return (
            "Отправить текущую ветку в настроенный upstream. Если upstream нет, "
            "будет предложен явный выбор remote и первый Push с -u."
        )

    def _make_action_button(self, text: str, object_name: str) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName(object_name)
        button.setEnabled(False)
        return button


def _branch_text(repository: Repository) -> str:
    if repository.is_unborn:
        branch = repository.current_branch or "новая ветка"
        return f"{branch} (коммитов нет)"
    if repository.is_detached:
        if repository.head_commit:
            return f"detached HEAD ({repository.head_commit[:7]})"
        return "detached HEAD"
    return repository.current_branch or _PLACEHOLDER


def _branch_tooltip(repository: Repository) -> str:
    if repository.is_unborn:
        return "В репозитории ещё нет коммитов"
    if repository.is_detached:
        return "HEAD не указывает на ветку (detached HEAD)"
    if repository.current_branch is None:
        return "Текущая ветка не определена"
    return f"Текущая ветка: {repository.current_branch}"
