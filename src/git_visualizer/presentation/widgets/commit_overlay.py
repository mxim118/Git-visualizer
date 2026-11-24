"""Небольшое окно ввода сообщения коммита поверх главного окна.

Поле сообщения вынесено из нижней панели Changes: в панели остаются только списки
Staged/Unstaged и кнопка Commit рядом с Diff. По нажатию Commit открывается этот
overlay с полем сообщения и кнопками Cancel/Commit. Виджет не запускает Git: он
только проверяет, что сообщение не пустое и не состоит из пробелов, и сообщает
готовый текст сигналом ``submitted(str)``; сам коммит выполняет MainWindow через
Application Services.

После успешного коммита MainWindow вызывает ``hide_overlay()``; при ошибке окно
остаётся открытым с введённым сообщением, чтобы пользователь мог исправить причину
и повторить. ``set_busy(True)`` блокирует ввод и кнопки на время фоновой операции.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

_INFO_COLOR = "#64748b"
_REASON_COLOR = "#b45309"
_CARD_WIDTH = 520
_CARD_MIN_HEIGHT = 250


class CommitOverlay(QWidget):
    """Окно сообщения коммита: поле ввода и кнопки Cancel/Commit."""

    submitted = Signal(str)  # сообщение коммита

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("commit_overlay")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("#commit_overlay { background: rgba(15, 23, 42, 110); }")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._staged_count = 0
        self._busy = False

        self.card = QFrame(self)
        self.card.setObjectName("commit_overlay_card")
        self.card.setStyleSheet(
            "#commit_overlay_card {"
            "  background: #ffffff;"
            "  border: 1px solid #94a3b8;"
            "  border-radius: 8px;"
            "}"
        )
        self.card.setFixedWidth(_CARD_WIDTH)
        self.card.setMinimumHeight(_CARD_MIN_HEIGHT)

        title = QLabel("Новый коммит", self.card)
        title.setObjectName("commit_overlay_title")
        title.setStyleSheet("font-weight: 600; font-size: 14px;")

        self.hint_label = QLabel(self.card)
        self.hint_label.setObjectName("commit_overlay_hint")
        self.hint_label.setWordWrap(True)
        self.hint_label.setStyleSheet(f"color: {_INFO_COLOR};")

        self.message_edit = QPlainTextEdit(self.card)
        self.message_edit.setObjectName("commit_overlay_message")
        self.message_edit.setPlaceholderText("Сообщение коммита")
        self.message_edit.setAccessibleName("Сообщение коммита")
        self.message_edit.setMinimumHeight(110)
        self.message_edit.textChanged.connect(self._update_state)

        self.cancel_button = QPushButton("Cancel", self.card)
        self.cancel_button.setObjectName("commit_overlay_cancel")
        self.cancel_button.clicked.connect(self._cancel)

        self.commit_button = QPushButton("Commit", self.card)
        self.commit_button.setObjectName("commit_overlay_commit")
        self.commit_button.clicked.connect(self._submit)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(8)
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.commit_button)

        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(10)
        card_layout.addWidget(title)
        card_layout.addWidget(self.hint_label)
        card_layout.addWidget(self.message_edit, 1)
        card_layout.addLayout(buttons)

        # Карточка по центру затемнённой области поверх всего главного окна.
        centered = QHBoxLayout()
        centered.setContentsMargins(0, 0, 0, 0)
        centered.addStretch(1)
        centered.addWidget(self.card)
        centered.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addStretch(1)
        layout.addLayout(centered)
        layout.addStretch(1)

        self._escape_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self._escape_shortcut.setContext(
            Qt.ShortcutContext.WidgetWithChildrenShortcut
        )
        self._escape_shortcut.activated.connect(self._cancel)

        self.hide()

    # --- публичный интерфейс ---

    def show_overlay(self, *, staged_count: int) -> None:
        """Открывает окно и показывает, сколько staged-файлов войдёт в коммит."""
        self._staged_count = max(0, staged_count)
        self._busy = False
        self._fit_parent()
        self._update_state()
        self.show()
        self.raise_()
        if self.message_edit.isEnabled():
            self.message_edit.setFocus(Qt.FocusReason.OtherFocusReason)
        else:
            self.setFocus(Qt.FocusReason.OtherFocusReason)

    def hide_overlay(self) -> None:
        """Закрывает окно и очищает сообщение (успех, отмена, смена репозитория)."""
        self._busy = False
        self.message_edit.clear()
        self.hide()

    def set_busy(self, busy: bool) -> None:
        """Блокирует ввод и кнопки на время выполнения коммита."""
        self._busy = busy
        self._update_state()

    def update_staged_count(self, count: int) -> None:
        """Обновляет количество staged-файлов после перезагрузки статуса."""
        self._staged_count = max(0, count)
        self._update_state()

    @property
    def message(self) -> str:
        return self.message_edit.toPlainText()

    @property
    def staged_count(self) -> int:
        return self._staged_count

    @property
    def is_busy(self) -> bool:
        return self._busy

    # --- внутренняя реализация ---

    def _update_state(self) -> None:
        has_message = bool(self.message_edit.toPlainText().strip())
        has_staged = self._staged_count > 0
        available = has_staged and not self._busy
        enabled = available and has_message

        self.message_edit.setEnabled(available)
        self.cancel_button.setEnabled(not self._busy)
        self.commit_button.setEnabled(enabled)

        if self._busy:
            hint = "Создание коммита…"
        elif not has_staged:
            hint = "Нет staged-изменений: добавьте файлы в индекс."
        elif not has_message:
            hint = "Введите сообщение коммита."
        else:
            hint = (
                "В коммит войдут только файлы из Staged "
                f"({self._staged_count})."
            )
        self.hint_label.setText(hint)
        self.hint_label.setStyleSheet(
            f"color: {_INFO_COLOR if enabled else _REASON_COLOR};"
        )
        self.commit_button.setToolTip(
            "Создать коммит только из файлов в Staged" if enabled else hint
        )

    def _submit(self) -> None:
        if not self.commit_button.isEnabled():
            return
        self.submitted.emit(self.message_edit.toPlainText())

    def _cancel(self) -> None:
        if self._busy:
            return
        self.hide_overlay()

    def _fit_parent(self) -> None:
        parent = self.parentWidget()
        if parent is not None:
            self.setGeometry(parent.rect())

    def showEvent(self, event) -> None:  # noqa: N802 (Qt API)
        self._fit_parent()
        super().showEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if event.key() == Qt.Key.Key_Escape:
            self._cancel()
            event.accept()
            return
        super().keyPressEvent(event)
