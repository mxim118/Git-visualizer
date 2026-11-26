"""Диалог добавления и изменения remote.

Виджет не запускает Git: он только собирает имя и URL. Значения передаются в
MainWindow/Application, где проверяются и записываются штатными командами Git.
Поддерживаются HTTPS- и SSH-адреса, а также локальные пути.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from git_visualizer.domain.models import Remote

_INFO_COLOR = "#64748b"


class RemoteEditDialog(QDialog):
    """Поля «Имя remote» и «URL репозитория» для добавления или изменения."""

    def __init__(
        self,
        *,
        remote: Remote | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._editing = remote is not None
        self.setObjectName("remote_edit_dialog")
        self.setWindowTitle(
            "Изменить remote" if self._editing else "Добавить remote"
        )
        self.setModal(True)
        self.setMinimumWidth(540)

        title = QLabel(
            "Изменить remote" if self._editing else "Новый remote", self
        )
        title.setObjectName("remote_edit_title")
        title.setStyleSheet("font-size: 16px; font-weight: 600;")

        hint = QLabel(
            "Поддерживаются HTTPS- и SSH-ссылки. Изменения записываются только "
            "в локальную конфигурацию Git: сеть и файлы репозитория не "
            "затрагиваются.",
            self,
        )
        hint.setObjectName("remote_edit_hint")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {_INFO_COLOR};")

        self.name_edit = QLineEdit(self)
        self.name_edit.setObjectName("remote_name_edit")
        self.name_edit.setPlaceholderText("Например, origin")
        self.name_edit.setAccessibleName("Имя remote")
        self.name_edit.setMaxLength(255)

        self.url_edit = QLineEdit(self)
        self.url_edit.setObjectName("remote_url_edit")
        self.url_edit.setPlaceholderText(
            "https://example.com/user/repo.git или git@example.com:user/repo.git"
        )
        self.url_edit.setAccessibleName("URL репозитория")
        self.url_edit.setMaxLength(2048)

        if remote is not None:
            self.name_edit.setText(remote.name)
            self.url_edit.setText(
                remote.fetch_url or remote.push_url or ""
            )

        form = QFormLayout()
        form.addRow("Имя remote:", self.name_edit)
        form.addRow("URL репозитория:", self.url_edit)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        self.save_button = self.buttons.button(
            QDialogButtonBox.StandardButton.Save
        )
        self.save_button.setText("Сохранить" if self._editing else "Добавить")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(
            "Отмена"
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addWidget(hint)
        layout.addLayout(form)
        layout.addWidget(self.buttons)

        self.name_edit.textChanged.connect(self._update_actions)
        self.url_edit.textChanged.connect(self._update_actions)
        self._update_actions()

        if self._editing:
            self.name_edit.selectAll()
        else:
            self.name_edit.setFocus()

    @property
    def remote_name(self) -> str:
        return self.name_edit.text().strip()

    @property
    def remote_url(self) -> str:
        return self.url_edit.text().strip()

    def _update_actions(self) -> None:
        self.save_button.setEnabled(
            bool(self.remote_name) and bool(self.remote_url)
        )
