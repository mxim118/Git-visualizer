"""Сообщение о том, что загружена не вся история, и кнопка догрузки (FR-HIST-07).

Панель показывается под графом, пока в репозитории остаются коммиты старше
загруженных: пользователь всегда видит, что история обрезана лимитом, а не
закончилась. Кнопка запрашивает следующую страницу; Git-команд виджет не
запускает — только сообщает о нажатии сигналом.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

_LOAD_MORE_TEXT = "Загрузить ещё"
_LOADING_TEXT = "Загрузка…"

_STYLE = (
    "QFrame#history_notice_bar {"
    "  background: #f1f5f9;"
    "  border-top: 1px solid #cbd5e1;"
    "}"
    "QLabel#history_notice_label {"
    "  color: #334155;"
    "  font-size: 11px;"
    "}"
)


class HistoryNoticeBar(QFrame):
    """Постоянное уведомление об обрезанной истории с кнопкой «Загрузить ещё».

    ``show_truncated`` показывает панель и сообщает, сколько коммитов загружено;
    ``show_loading`` блокирует кнопку на время запроса, чтобы страницы не
    запрашивались параллельно; ``hide`` скрывает панель, когда загружена вся
    история или репозиторий закрыт.
    """

    load_more_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("history_notice_bar")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet(_STYLE)

        self._label = QLabel(self)
        self._label.setObjectName("history_notice_label")
        self._label.setWordWrap(True)

        self._button = QPushButton(_LOAD_MORE_TEXT, self)
        self._button.setObjectName("history_load_more_button")
        self._button.setToolTip(
            "Загрузить следующую страницу более старых коммитов"
        )
        self._button.clicked.connect(self.load_more_requested)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)
        layout.addWidget(self._label, 1)
        layout.addWidget(self._button)

        self.hide()

    @property
    def label(self) -> QLabel:
        return self._label

    @property
    def button(self) -> QPushButton:
        return self._button

    @property
    def is_loading(self) -> bool:
        """Идёт ли загрузка следующей страницы (кнопка заблокирована)."""
        return not self._button.isEnabled()

    def show_truncated(self, shown_count: int) -> None:
        """Показывает, что загружены последние ``shown_count`` коммитов не все."""
        self._label.setText(
            f"Показаны последние {shown_count} коммитов — в репозитории есть "
            "более старая история."
        )
        self._button.setText(_LOAD_MORE_TEXT)
        self._button.setEnabled(True)
        self.show()

    def show_loading(self) -> None:
        """Показывает загрузку страницы и не даёт нажать кнопку повторно."""
        self._label.setText("Загрузка более старой истории…")
        self._button.setText(_LOADING_TEXT)
        self._button.setEnabled(False)
        self.show()
