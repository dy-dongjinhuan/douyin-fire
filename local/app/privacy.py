from __future__ import annotations

import logging
from typing import Iterable, Mapping


def target_alias(index: int) -> str:
    """兼容旧调用，返回占位别名（不再用于日志脱敏）。"""
    return f"好友{index + 1:02d}"


def build_target_aliases(targets: Iterable[object]) -> dict[str, str]:
    """构建 真实名 -> 显示名 的映射。

    按需求：日志中直接显示用户在配置里填写的好友 ID（真实 name），
    因此别名直接等于真实名称，等价于不脱敏。保留映射结构以免其它调用方出错。
    """
    aliases: dict[str, str] = {}
    for index, target in enumerate(targets):
        name = getattr(target, "name", None)
        if name is None and isinstance(target, str):
            name = target
        if name:
            aliases[name] = name
    return aliases


def redact_text(text: str, aliases: Mapping[str, str]) -> str:
    """Replace every real name in ``text`` with its alias.

    Longer names are replaced first so that a name like ``小明同学`` is fully
    redacted before its prefix ``小明`` is considered.
    """
    for name in sorted((name for name in aliases if name), key=len, reverse=True):
        text = text.replace(name, aliases[name])
    return text


class RedactingFormatter(logging.Formatter):
    """Formatter that redacts real friend names as a final safety net.

    It redacts the complete formatted string (message plus any traceback) so
    that unexpected text such as Playwright locator messages containing a real
    nickname never reaches public logs.
    """

    def __init__(
        self,
        fmt: str | None = None,
        datefmt: str | None = None,
        aliases: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(fmt, datefmt)
        self.aliases = dict(aliases or {})

    def format(self, record: logging.LogRecord) -> str:
        return redact_text(super().format(record), self.aliases)