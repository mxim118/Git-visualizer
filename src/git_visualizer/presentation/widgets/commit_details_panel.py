"""Правая панель: детали выбранного коммита.

Список изменённых файлов и diff появятся в следующих итерациях; здесь оставлены
явные заглушки. Панель ничего не запрашивает у Git — данные передаёт Presentation.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFormLayout,
    QFrame,
    QLabel,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from git_visualizer.domain.models import Commit

_PLACEHOLDER_TEXT = "Коммит не выбран"
_EMPTY_REPOSITORY_TEXT = "В репозитории пока нет коммитов."
_CHANGED_FILES_STUB = "Список изменённых файлов появится в следующих версиях."


class CommitDetailsPanel(QWidget):
    """Сведения о выбранном коммите с заглушкой для списка изменённых файлов."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("commit_details_panel")

        self._current_commit: Commit | None = None

        title = QLabel("Детали коммита")
        title.setObjectName("commit_details_title")
        title.setStyleSheet("font-weight: 600;")

        self.placeholder_label = QLabel(_PLACEHOLDER_TEXT)
        self.placeholder_label.setObjectName("commit_details_placeholder")
        self.placeholder_label.setWordWrap(True)
        self.placeholder_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        self.placeholder_label.setStyleSheet("color: #64748b;")
        self.placeholder_label.setContentsMargins(8, 8, 8, 8)

        self.hash_value = self._value_label("commit_hash_value")
        self.author_value = self._value_label("commit_author_value")
        self.date_value = self._value_label("commit_date_value")
        self.message_value = self._value_label("commit_message_value")

        content = QWidget()
        form = QFormLayout(content)
        form.setContentsMargins(8, 8, 8, 8)
        form.setSpacing(8)
        form.addRow("Hash", self.hash_value)
        form.addRow("Автор", self.author_value)
        form.addRow("Дата", self.date_value)
        form.addRow("Сообщение", self.message_value)
        form.addRow(self._changed_files_stub())

        self._stack = QStackedWidget(self)
        self._stack.setObjectName("commit_details_stack")
        self._stack.addWidget(self.placeholder_label)
        self._stack.addWidget(content)
        self._content = content

        scroll = QScrollArea(self)
        scroll.setObjectName("commit_details_scroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self._stack)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        layout.addWidget(title)
        layout.addWidget(scroll, 1)

        self.clear()

    def show_commit(self, commit: Commit) -> None:
        """Показывает hash, автора, дату и сообщение выбранного коммита."""
        self._current_commit = commit
        self.hash_value.setText(commit.hash)
        self.hash_value.setToolTip(commit.hash)
        self.author_value.setText(f"{commit.author_name} <{commit.author_email}>")
        self.date_value.setText(_format_datetime(commit.authored_at))
        self.message_value.setText(_full_message(commit))
        self._stack.setCurrentWidget(self._content)

    def show_empty_repository(self) -> None:
        """Пустое состояние для репозитория без коммитов."""
        self._current_commit = None
        self.placeholder_label.setText(_EMPTY_REPOSITORY_TEXT)
        self._stack.setCurrentWidget(self.placeholder_label)

    def clear(self) -> None:
        """Сбрасывает панель: коммит не выбран."""
        self._current_commit = None
        self.placeholder_label.setText(_PLACEHOLDER_TEXT)
        self.hash_value.clear()
        self.author_value.clear()
        self.date_value.clear()
        self.message_value.clear()
        self._stack.setCurrentWidget(self.placeholder_label)

    @property
    def current_commit(self) -> Commit | None:
        return self._current_commit

    @property
    def placeholder_text(self) -> str:
        return self.placeholder_label.text()

    @staticmethod
    def _value_label(object_name: str) -> QLabel:
        label = QLabel()
        label.setObjectName(object_name)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        return label

    @staticmethod
    def _changed_files_stub() -> QWidget:
        stub = QWidget()
        layout = QVBoxLayout(stub)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(4)
        title = QLabel("Изменённые файлы")
        title.setStyleSheet("font-weight: 600;")
        note = QLabel(_CHANGED_FILES_STUB)
        note.setObjectName("changed_files_stub")
        note.setWordWrap(True)
        note.setStyleSheet("color: #94a3b8;")
        layout.addWidget(title)
        layout.addWidget(note)
        return stub


def _format_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        return value.strftime("%d.%m.%Y %H:%M:%S")
    return value.strftime("%d.%m.%Y %H:%M:%S %z")


def _full_message(commit: Commit) -> str:
    if commit.body:
        return f"{commit.subject}\n\n{commit.body}"
    return commit.subject
