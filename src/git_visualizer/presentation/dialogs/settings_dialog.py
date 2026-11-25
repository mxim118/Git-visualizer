"""Диалог локальных настроек приложения."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from git_visualizer.presentation.settings import (
    HISTORY_LIMIT_STEP,
    MAX_HISTORY_LIMIT,
    MIN_HISTORY_LIMIT,
)


class SettingsDialog(QDialog):
    """Изменяет сохраняемые настройки, которые не относятся к открытому репозиторию.

    Сейчас это только лимит первоначальной загрузки коммитов: он применяется к
    следующим загрузкам истории. Изменённое значение сохраняет и передаёт
    контроллеру главное окно — сам диалог ничего не пишет в ``QSettings``.
    """

    def __init__(
        self,
        *,
        history_limit: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("settings_dialog")
        self.setWindowTitle("Настройки")
        self.setModal(True)

        title = QLabel("Настройки приложения")
        title.setStyleSheet("font-size: 16px; font-weight: 600;")

        hint = QLabel(
            "Лимит загрузки коммитов ограничивает историю, которая загружается "
            "при открытии репозитория и при обновлении. Большее значение "
            "показывает больше истории, но загружается дольше."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #475569;")

        self.history_limit_spin = QSpinBox(self)
        self.history_limit_spin.setObjectName("history_limit_spin")
        self.history_limit_spin.setRange(MIN_HISTORY_LIMIT, MAX_HISTORY_LIMIT)
        self.history_limit_spin.setSingleStep(HISTORY_LIMIT_STEP)
        self.history_limit_spin.setValue(history_limit)

        form = QFormLayout()
        form.addRow("Загружать коммитов:", self.history_limit_spin)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Сохранить")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addWidget(hint)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def history_limit(self) -> int:
        """Лимит, выбранный пользователем."""
        return self.history_limit_spin.value()
