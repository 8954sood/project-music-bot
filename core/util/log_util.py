from __future__ import annotations

from datetime import datetime
import re
import sys
import traceback
from typing import Any, Optional


_SECRET_PATTERNS = (
    (
        re.compile(r"(?i)\b(bearer)(\s+)([A-Za-z0-9._~+/=-]+)"),
        r"\1\2[REDACTED]",
    ),
    (
        re.compile(
            r"(?i)\b(authorization|cookie|password|refresh_token|po_token|potoken)"
            r"(\s*[:=]\s*)([^\s,;]+)"
        ),
        r"\1\2[REDACTED]",
    ),
    (
        re.compile(
            r"(?i)([?&](?:token|key|sig|signature|auth|authorization|password|"
            r"refresh_token|po_token|potoken)=)([^&#\s]+)"
        ),
        r"\1[REDACTED]",
    ),
)


def _redact(value: Any) -> str:
    text = "-" if value is None else str(value)
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _write_log(func_name: str, value: Any) -> None:
    timestamp = datetime.now().astimezone().isoformat(timespec="milliseconds")
    print(f"{timestamp} [{func_name}] : {_redact(value)}", flush=True)


def log_event(user_input: Optional[Any]) -> None:
    func_name = sys._getframe(1).f_code.co_name
    # flush=True: nohup 등 비-tty 로 출력이 리다이렉트되면 stdout 이 블록 버퍼링되어 로그가
    # 즉시 안 보인다. 매 로그를 flush 해 `python -u` / PYTHONUNBUFFERED 없이도 실시간으로 남긴다.
    _write_log(func_name, user_input)


def log_exception(message: str, exc: BaseException) -> None:
    """예외 타입, cause chain, traceback을 한 요청 로그에 남긴다."""
    func_name = sys._getframe(1).f_code.co_name
    _write_log(
        func_name,
        f"{message} exception_type={type(exc).__name__} error={exc}",
    )
    formatted = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    _write_log(func_name, f"{message} traceback=\n{formatted.rstrip()}")
