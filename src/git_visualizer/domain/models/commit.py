"""Модель коммита."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, kw_only=True)
class Commit:
    """Коммит истории.

    ``parent_hashes`` — кортеж: пустой у root-коммита, один hash у обычного,
    два и более у merge-коммита; порядок как в Git (первый родитель — первым).

    Даты рекомендуется хранить с часовым поясом (aware datetime): Git сохраняет смещение.

    ``refs`` — имена refs, указывающих на этот коммит. Формат имён (короткие или
    полные) определяет слой Infrastructure.
    """

    hash: str
    short_hash: str
    parent_hashes: tuple[str, ...]
    author_name: str
    author_email: str
    authored_at: datetime
    committer_name: str
    committer_email: str
    committed_at: datetime
    subject: str
    body: str
    refs: tuple[str, ...] = ()

    @property
    def is_root(self) -> bool:
        return not self.parent_hashes

    @property
    def is_merge(self) -> bool:
        return len(self.parent_hashes) > 1
