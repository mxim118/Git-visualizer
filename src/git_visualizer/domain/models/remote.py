"""Модель удалённого репозитория (remote)."""

from dataclasses import dataclass


@dataclass(frozen=True, kw_only=True)
class Remote:
    """Настроенный remote.

    URL предназначены только для отображения: Infrastructure удаляет из http(s)-адресов
    логин и пароль. Операции (Fetch, Pull, Push) выполняются по имени remote, а не по URL.
    ``None`` — адрес не задан.
    """

    name: str
    fetch_url: str | None = None
    push_url: str | None = None
