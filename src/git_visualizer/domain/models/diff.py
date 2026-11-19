"""Модель diff одного файла."""

from dataclasses import dataclass


@dataclass(frozen=True, kw_only=True)
class Diff:
    """Результат сравнения одного файла; хранит текст в формате unified diff.

    Построчный разбор не выполняется: подсветку добавленных и удалённых строк
    выполняет Presentation по тексту.

    * ``is_binary`` — текстовый diff недоступен (``text`` пустой);
    * ``is_truncated`` — текст обрезан из-за большого размера;
    * ``base_commit_hash`` — коммит, с которым выполнено сравнение: родитель выбранного
      коммита (для merge-коммита интерфейс обязан показать, какой именно). ``None`` —
      для diff рабочей директории и для root-коммита (сравнение с пустым деревом).
    """

    path: str
    text: str
    old_path: str | None = None
    is_binary: bool = False
    is_truncated: bool = False
    base_commit_hash: str | None = None
