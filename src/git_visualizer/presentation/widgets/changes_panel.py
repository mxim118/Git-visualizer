"""Нижняя панель Changes: заглушка для staged/unstaged изменений.

Stage, Unstage, diff и создание коммитов появятся в следующих итерациях; сейчас
панель только обозначает место в интерфейсе.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

_STUB_TEXT = "Функциональность появится в следующих версиях."


class ChangesPanel(QWidget):
    """Панель изменений рабочей директории с областями Unstaged и Staged."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("changes_panel")

        title = QLabel("Изменения")
        title.setObjectName("changes_panel_title")
        title.setStyleSheet("font-weight: 600;")

        unstaged = self._make_stub_group("Unstaged", "unstaged_changes_group")
        staged = self._make_stub_group("Staged", "staged_changes_group")

        groups = QHBoxLayout()
        groups.setContentsMargins(0, 0, 0, 0)
        groups.setSpacing(8)
        groups.addWidget(unstaged, 1)
        groups.addWidget(staged, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 8)
        layout.setSpacing(6)
        layout.addWidget(title)
        layout.addLayout(groups, 1)

    @staticmethod
    def _make_stub_group(title: str, object_name: str) -> QGroupBox:
        group = QGroupBox(title)
        group.setObjectName(object_name)
        layout = QVBoxLayout(group)
        label = QLabel(_STUB_TEXT)
        label.setObjectName(f"{object_name}_stub")
        label.setWordWrap(True)
        label.setStyleSheet("color: #94a3b8;")
        layout.addWidget(label)
        layout.addStretch(1)
        return group
