"""Полноэкранная панель diff выбранного файла.

Одна панель используется и для файла коммита (правая панель «Изменённые файлы»),
и для файла рабочей директории (нижняя панель Changes): встроенные diff слишком
малы, поэтому файл открывается крупной секцией поверх всего окна с крестиком.
Виджет только рисует готовый ``Diff`` — Git не запускает; данные передаёт
``MainWindow`` после фоновой загрузки.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from git_visualizer.domain.enums import FileChangeType
from git_visualizer.domain.models import ChangedFile, Commit, Diff, FileChange
from git_visualizer.presentation.widgets.diff_view import DiffView

_CHANGE_LABELS: dict[FileChangeType, str] = {
    FileChangeType.ADDED: "добавлен",
    FileChangeType.MODIFIED: "изменён",
    FileChangeType.DELETED: "удалён",
    FileChangeType.RENAMED: "переименован",
    FileChangeType.COPIED: "скопирован",
    FileChangeType.TYPE_CHANGED: "тип изменён",
}

UNTRACKED_NOTICE = (
    "Файл не отслеживается Git: текстовый diff недоступен, пока файл не добавлен "
    "в индекс."
)
_STAGED_BASE_TEXT = "Staged diff: индекс относительно HEAD."
_UNSTAGED_BASE_TEXT = "Unstaged diff: рабочая директория относительно индекса."
_CONFLICT_BASE_TEXT = "Конфликт (unmerged): показан объединённый diff Git."


class DiffOverlay(QWidget):
    """Большая панель diff поверх окна; закрывается крестиком или клавишей Esc."""

    close_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("diff_overlay")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            "#diff_overlay {"
            "  background: #ffffff;"
            "  border: 1px solid #94a3b8;"
            "}"
        )
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        title = QLabel("Diff файла")
        title.setObjectName("diff_overlay_title")
        title.setStyleSheet("font-weight: 600; font-size: 14px;")

        self.close_button = QPushButton("✕", self)
        self.close_button.setObjectName("diff_overlay_close")
        self.close_button.setFixedSize(28, 28)
        self.close_button.setToolTip("Закрыть diff (Esc)")
        self.close_button.clicked.connect(self._close)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.close_button)

        self.diff_view = DiffView(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(6)
        layout.addLayout(header)
        layout.addWidget(self.diff_view, 1)

        self.hide()

    # --- файл коммита ---

    def show_commit_loading(self, commit: Commit, file_change: FileChange) -> None:
        """Открывает панель и показывает, что diff коммита загружается."""
        self._show(
            "Загрузка diff…",
            title=commit_diff_title(file_change),
            base_text=commit_diff_base_text(commit, None),
        )

    def show_commit_diff(
        self, commit: Commit, file_change: FileChange, diff: Diff
    ) -> None:
        """Показывает готовый diff файла коммита."""
        self._open()
        self.diff_view.show_diff(
            diff,
            title=commit_diff_title(file_change),
            base_text=commit_diff_base_text(commit, diff),
        )

    def show_commit_error(
        self, commit: Commit, file_change: FileChange, message: str
    ) -> None:
        self._show(
            message,
            title=commit_diff_title(file_change),
            base_text=commit_diff_base_text(commit, None),
        )

    # --- файл рабочей директории ---

    def show_worktree_loading(
        self, changed_file: ChangedFile, staged: bool
    ) -> None:
        """Открывает панель и показывает, что diff рабочей директории загружается."""
        self._show(
            "Загрузка diff…",
            title=worktree_diff_title(changed_file, staged),
            base_text=worktree_diff_base_text(changed_file, staged),
        )

    def show_worktree_diff(
        self, changed_file: ChangedFile, staged: bool, diff: Diff
    ) -> None:
        """Показывает готовый diff файла из индекса или рабочей директории."""
        self._open()
        self.diff_view.show_diff(
            diff,
            title=worktree_diff_title(changed_file, staged),
            base_text=worktree_diff_base_text(changed_file, staged),
        )

    def show_worktree_error(
        self, changed_file: ChangedFile, staged: bool, message: str
    ) -> None:
        self._show(
            message,
            title=worktree_diff_title(changed_file, staged),
            base_text=worktree_diff_base_text(changed_file, staged),
        )

    def show_untracked(self, changed_file: ChangedFile, staged: bool) -> None:
        """Пояснение для неотслеживаемого файла: Git diff по нему не строит."""
        self._show(
            UNTRACKED_NOTICE,
            title=worktree_diff_title(changed_file, staged),
            base_text=worktree_diff_base_text(changed_file, staged),
        )

    # --- общее ---

    def hide_overlay(self) -> None:
        """Скрывает панель, не сообщая о закрытии (смена коммита/репозитория)."""
        self.hide()

    def _show(self, message: str, *, title: str, base_text: str) -> None:
        self._open()
        self.diff_view.show_message(message, title=title, base_text=base_text)

    def _open(self) -> None:
        self._fit_parent()
        self.show()
        self.raise_()
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    def _close(self) -> None:
        self.hide()
        self.close_requested.emit()

    def _fit_parent(self) -> None:
        parent = self.parentWidget()
        if parent is not None:
            self.setGeometry(parent.rect())

    def showEvent(self, event) -> None:  # noqa: N802 (Qt API)
        self._fit_parent()
        super().showEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if event.key() == Qt.Key.Key_Escape:
            self._close()
            event.accept()
            return
        super().keyPressEvent(event)


def commit_diff_title(file_change: FileChange) -> str:
    return f"{_display_path(file_change)} — {_change_label(file_change)}"


def commit_diff_base_text(commit: Commit, diff: Diff | None) -> str:
    """Явная база сравнения коммита: merge — первый родитель, root — пустое дерево."""
    base_hash = diff.base_commit_hash if diff is not None else _first_parent(commit)
    if base_hash is None:
        return "Сравнение с пустым деревом (root-коммит)."
    short = base_hash[:7]
    if commit.is_merge:
        return f"Сравнение с первым родителем {short} (merge-коммит)."
    return f"Сравнение с родителем {short}."


def worktree_diff_title(changed_file: ChangedFile, staged: bool) -> str:
    section = "в индексе" if staged else "вне индекса"
    return f"{_display_path(changed_file)} — {section}"


def worktree_diff_base_text(changed_file: ChangedFile, staged: bool) -> str:
    if changed_file.is_conflicted:
        return _CONFLICT_BASE_TEXT
    return _STAGED_BASE_TEXT if staged else _UNSTAGED_BASE_TEXT


def _display_path(change: ChangedFile | FileChange) -> str:
    if change.old_path:
        return f"{change.old_path} → {change.path}"
    return change.path


def _change_label(file_change: FileChange) -> str:
    return _CHANGE_LABELS.get(file_change.change_type, file_change.change_type.value)


def _first_parent(commit: Commit) -> str | None:
    return commit.parent_hashes[0] if commit.parent_hashes else None
