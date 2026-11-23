"""Стартовый экран: открытие репозитория и состояние окружения Git."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from git_visualizer.application.services.git_environment_service import GitEnvironment
from git_visualizer.presentation.error_texts import GIT_INSTALL_URL

_NEUTRAL_COLOR = "#475569"
_SUCCESS_COLOR = "#15803d"
_WARNING_COLOR = "#b45309"
_ERROR_COLOR = "#b91c1c"

# Ширина карточки и её внутренней области (карточка фиксирована, чтобы подписи
# не пересчитывали высоту через heightForWidth, что приводило к обрезке текста).
_CARD_WIDTH = 680
_CARD_PADDING = 32
_CARD_BORDER = 1  # рамка QFrame.Shape.StyledPanel
_CONTENT_WIDTH = _CARD_WIDTH - 2 * (_CARD_PADDING + _CARD_BORDER)
_MIN_STATUS_HEIGHT = 12


class StartScreen(QWidget):
    """Экран до открытия репозитория.

    Показывает кнопку выбора папки, краткую подсказку и результат проверки Git.
    Папку выбирает MainWindow (стандартный диалог), экран только сообщает о запросе.
    """

    open_repository_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("start_screen")

        card = QFrame(self)
        card.setObjectName("start_screen_card")
        card.setFixedWidth(_CARD_WIDTH)
        card.setFrameShape(QFrame.Shape.StyledPanel)
        self._card = card

        title = QLabel("Git Visualizer")
        title.setObjectName("start_screen_title")
        title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        title.setStyleSheet("font-size: 28px; font-weight: 600;")

        subtitle = QLabel("Просмотр истории Git-репозитория и графа коммитов")
        subtitle.setObjectName("start_screen_subtitle")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        subtitle.setWordWrap(False)
        subtitle.setStyleSheet("color: #475569;")

        self.open_button = QPushButton("Открыть репозиторий")
        self.open_button.setObjectName("open_repository_button")
        self.open_button.setMinimumHeight(40)
        self.open_button.setMinimumWidth(240)
        self.open_button.clicked.connect(self.open_repository_requested)

        hint = QLabel(
            "Выберите папку проекта с Git-репозиторием.\n"
            "Можно выбрать вложенную папку —\n"
            "приложение само найдёт корень.\n"
            "Bare-репозитории не поддерживаются."
        )
        hint.setObjectName("start_screen_hint")
        hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        hint.setWordWrap(False)
        hint.setStyleSheet("color: #64748b;")

        self.git_status_label = QLabel("Проверка Git…")
        self.git_status_label.setObjectName("git_status_label")
        self.git_status_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.git_status_label.setWordWrap(True)
        self.git_status_label.setFixedWidth(_CONTENT_WIDTH)
        self.git_status_label.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum
        )
        self.git_status_label.setStyleSheet(f"color: {_NEUTRAL_COLOR};")

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(
            _CARD_PADDING, 28, _CARD_PADDING, 28
        )
        card_layout.setSpacing(14)
        card_layout.addWidget(title)
        card_layout.addWidget(subtitle)
        card_layout.addSpacing(8)
        card_layout.addWidget(self.open_button, 0, Qt.AlignmentFlag.AlignHCenter)
        card_layout.addWidget(hint)
        card_layout.addWidget(self.git_status_label)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.addStretch(1)
        outer.addWidget(card, 0, Qt.AlignmentFlag.AlignHCenter)
        outer.addStretch(1)

        self._update_status_height()

    def set_open_enabled(self, enabled: bool) -> None:
        self.open_button.setEnabled(enabled)

    def show_git_checking(self) -> None:
        self._set_git_status("Проверка Git…", _NEUTRAL_COLOR)

    def show_git_environment(self, environment: GitEnvironment) -> None:
        """Показывает найденную версию Git и предупреждение, если она устарела."""
        if environment.is_supported:
            self._set_git_status(
                f"Git {environment.version_text} — версия поддерживается.",
                _SUCCESS_COLOR,
            )
            return
        self._set_git_status(
            f"Обнаружена версия Git {environment.version_text}. Для работы требуется "
            f"Git {environment.minimum_text} или новее. Обновите Git: {GIT_INSTALL_URL}",
            _WARNING_COLOR,
        )

    def show_git_error(self, message: str) -> None:
        self._set_git_status(message, _ERROR_COLOR)

    def _set_git_status(self, text: str, color: str) -> None:
        self.git_status_label.setText(text)
        self.git_status_label.setStyleSheet(f"color: {color};")
        self._update_status_height()

    def _update_status_height(self) -> None:
        """Выделяет подписи столько строк, сколько нужно при её ширине.

        ``QLabel`` с переносом слов не сообщает layout корректную высоту при
        фиксированной ширине, из-за чего текст обрезался. Высота считается для
        фактической ширины содержимого карточки.
        """
        height = max(
            self.git_status_label.heightForWidth(_CONTENT_WIDTH),
            _MIN_STATUS_HEIGHT,
        )
        self.git_status_label.setFixedHeight(height)
        self.git_status_label.updateGeometry()

        # Высота карточки должна учитывать новую высоту подписи сразу:
        # без этого Qt оставляет прежнюю высоту, и текст обрезается/наезжает.
        card_layout = self._card.layout()
        if card_layout is None:
            return
        card_layout.activate()
        self._card.setMinimumHeight(card_layout.sizeHint().height())
        self._card.updateGeometry()
        outer_layout = self.layout()
        if outer_layout is not None:
            outer_layout.activate()
