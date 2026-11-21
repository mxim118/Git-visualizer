"""Разбор вывода Git в доменные модели (без запуска процессов)."""

import re

from git_visualizer.domain.models import Remote

_FETCH_SUFFIX = " (fetch)"
_PUSH_SUFFIX = " (push)"

# Логин и пароль в http(s)-адресе: https://user:secret@host/... Жадное совпадение
# до последнего «@» в authority-части (в пароле может встретиться «@»).
_HTTP_CREDENTIALS = re.compile(r"^(https?://)[^/?#]*@", re.IGNORECASE)


def strip_url_credentials(url: str) -> str:
    """Удаляет логин и пароль из http(s)-адреса.

    SSH-адреса (``git@github.com:org/repo.git``, ``ssh://git@host/...``) не меняются:
    имя пользователя там не секрет.
    """
    return _HTTP_CREDENTIALS.sub(r"\1", url)


def parse_remotes(output: str) -> tuple[Remote, ...]:
    """Разбирает вывод ``git remote -v``.

    Формат строк: ``<name><TAB><url> (fetch)`` и ``<name><TAB><url> (push)``. Remote без
    адресов выводится строкой ``<name><TAB>`` без суффикса. Порядок remote — как в выводе Git.
    Если push-адресов несколько, берётся первый.
    """
    names: dict[str, None] = {}  # dict как упорядоченное множество
    fetch_urls: dict[str, str] = {}
    push_urls: dict[str, str] = {}

    for line in output.split("\n"):
        line = line.rstrip("\r")
        if not line:
            continue
        name, _, rest = line.partition("\t")
        names.setdefault(name)
        if rest.endswith(_FETCH_SUFFIX):
            url = rest.removesuffix(_FETCH_SUFFIX)
            if url:
                fetch_urls.setdefault(name, strip_url_credentials(url))
        elif rest.endswith(_PUSH_SUFFIX):
            url = rest.removesuffix(_PUSH_SUFFIX)
            if url:
                push_urls.setdefault(name, strip_url_credentials(url))

    return tuple(
        Remote(name=name, fetch_url=fetch_urls.get(name), push_url=push_urls.get(name))
        for name in names
    )
