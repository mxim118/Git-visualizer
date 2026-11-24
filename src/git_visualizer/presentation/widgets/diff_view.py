"""Просмотр unified diff: подсветка добавленных и удалённых строк.

Виджет только отображает готовый ``Diff``; Git здесь не запускается. Для больших
diff Application заранее обрезает текст и выставляет ``is_truncated``.
"""

from __future__ import annotations

from enum import Enum

from PySide6.QtCore import Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QLabel,
    QPlainTextEdit,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from git_visualizer.domain.models import Diff

ADDED_BACKGROUND = QColor("#dcfce7")
DELETED_BACKGROUND = QColor("#fee2e2")
HUNK_COLOR = QColor("#1d4ed8")
HEADER_COLOR = QColor("#64748b")
NOTE_COLOR = QColor("#b45309")

_EMPTY_DIFF_TEXT = "Для этого файла нет текстового diff."
_BINARY_DIFF_TEXT = "Бинарный файл: текстовый diff недоступен."
_TRUNCATED_TEXT = "Diff обрезан: показана только часть изменений."


class DiffLineKind(Enum):
    """Вид строки unified diff для подсветки."""

    ADDED = "added"
    DELETED = "deleted"
    HUNK = "hunk"
    HEADER = "header"
    NOTE = "note"
    CONTEXT = "context"


def classify_diff_line(line: str) -> DiffLineKind:
    """Определяет вид строки diff по префиксу.

    Заголовки файлов (``---``/``+++``) проверяются раньше строк с ``-``/``+``,
    иначе они выглядели бы удалённой/добавленной строкой. ``diff --cc`` и
    ``diff --combined`` — заголовки объединённого diff конфликтующего файла.
    """
    if line.startswith("diff --"):
        return DiffLineKind.HEADER
    if line.startswith(("index ", "new file ", "deleted file ",
                        "similarity ", "dissimilarity ", "rename ", "copy ",
                        "old mode ", "new mode ", "* Unmerged path ")):
        return DiffLineKind.HEADER
    if line.startswith(("--- ", "+++ ")):
        return DiffLineKind.HEADER
    if line.startswith("@@"):
        return DiffLineKind.HUNK
    if line.startswith("+"):
        return DiffLineKind.ADDED
    if line.startswith("-"):
        return DiffLineKind.DELETED
    if line.startswith("\\ No newline at end of file"):
        return DiffLineKind.NOTE
    return DiffLineKind.CONTEXT


class _DiffHighlighter(QSyntaxHighlighter):
    """Подсвечивает строки diff: добавленные — зелёным, удалённые — красным."""

    def __init__(self, document) -> None:
        super().__init__(document)
        self._formats = {
            DiffLineKind.ADDED: _format(ADDED_BACKGROUND, QColor("#14532d")),
            DiffLineKind.DELETED: _format(DELETED_BACKGROUND, QColor("#7f1d1d")),
            DiffLineKind.HUNK: _format(QColor("#eff6ff"), HUNK_COLOR, bold=True),
            DiffLineKind.HEADER: _format(None, HEADER_COLOR),
            DiffLineKind.NOTE: _format(None, NOTE_COLOR),
        }

    def highlightBlock(self, text: str) -> None:
        result = self._formats.get(classify_diff_line(text))
        if result is not None:
            self.setFormat(0, len(text), result)


def _format(
    background: QColor | None,
    foreground: QColor,
    *,
    bold: bool = False,
) -> QTextCharFormat:
    char_format = QTextCharFormat()
    char_format.setForeground(foreground)
    if background is not None:
        char_format.setBackground(background)
    if bold:
        char_format.setFontWeight(QFont.Weight.Bold)
    return char_format


class DiffView(QWidget):
    """Панель diff: заголовок, база сравнения, уведомление и текст."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("diff_view")
        self._last_diff: Diff | None = None

        self.header_label = QLabel()
        self.header_label.setObjectName("diff_header")
        self.header_label.setStyleSheet("font-weight: 600;")
        self.header_label.setWordWrap(True)

        self.base_label = QLabel()
        self.base_label.setObjectName("diff_base")
        self.base_label.setStyleSheet("color: #64748b;")
        self.base_label.setWordWrap(True)

        self.notice_label = QLabel()
        self.notice_label.setObjectName("diff_notice")
        self.notice_label.setStyleSheet(
            "color: #92400e; background: #fef3c7; padding: 4px;"
        )
        self.notice_label.setWordWrap(True)
        self.notice_label.hide()

        self.message_label = QLabel()
        self.message_label.setObjectName("diff_message")
        self.message_label.setWordWrap(True)
        self.message_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )

        self.editor = QPlainTextEdit()
        self.editor.setObjectName("diff_text")
        self.editor.setReadOnly(True)
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.editor.setFont(
            QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        )
        self.editor.setTabStopDistance(40)
        self._highlighter = _DiffHighlighter(self.editor.document())

        self.stack = QStackedWidget()
        self.stack.setObjectName("diff_stack")
        self.stack.addWidget(self.editor)
        self.stack.addWidget(self.message_label)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self.header_label)
        layout.addWidget(self.base_label)
        layout.addWidget(self.notice_label)
        layout.addWidget(self.stack, 1)

        self.clear()

    @property
    def text(self) -> str:
        return self.editor.toPlainText()

    @property
    def message_text(self) -> str:
        return self.message_label.text()

    @property
    def notice_text(self) -> str:
        return self.notice_label.text() if not self.notice_label.isHidden() else ""

    @property
    def is_editor_visible(self) -> bool:
        return self.stack.currentWidget() is self.editor

    def show_diff(self, diff: Diff, *, title: str, base_text: str) -> None:
        """Показывает готовый diff; бинарный и пустой текст — сообщением."""
        self._last_diff = diff
        self.header_label.setText(title)
        self.base_label.setText(base_text)
        self.notice_label.setVisible(diff.is_truncated)
        if diff.is_truncated:
            self.notice_label.setText(_TRUNCATED_TEXT)

        if diff.is_binary:
            self._show_message(_BINARY_DIFF_TEXT)
            return
        if not diff.text.strip():
            self._show_message(_EMPTY_DIFF_TEXT)
            return

        self.editor.setPlainText(diff.text)
        self.editor.moveCursor(QTextCursor.MoveOperation.Start)
        self.stack.setCurrentWidget(self.editor)

    def show_message(
        self, message: str, *, title: str = "", base_text: str = ""
    ) -> None:
        """Показывает сообщение вместо текста (загрузка, ошибка, пустое состояние)."""
        self._last_diff = None
        self.header_label.setText(title)
        self.base_label.setText(base_text)
        self.notice_label.hide()
        self._show_message(message)

    def clear(self, message: str = "Выберите файл, чтобы увидеть diff.") -> None:
        self.show_message(message)

    def _show_message(self, message: str) -> None:
        self.message_label.setText(message)
        self.stack.setCurrentWidget(self.message_label)
