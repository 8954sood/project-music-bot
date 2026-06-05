from __future__ import annotations

from datetime import datetime
import sys
from typing import Any, Optional


def log_event(user_input: Optional[Any]) -> None:
    timestamp = datetime.now().isoformat(timespec="seconds")
    func_name = sys._getframe(1).f_code.co_name
    log_input = "-" if user_input is None else str(user_input)
    # flush=True: nohup 등 비-tty 로 출력이 리다이렉트되면 stdout 이 블록 버퍼링되어 로그가
    # 즉시 안 보인다. 매 로그를 flush 해 `python -u` / PYTHONUNBUFFERED 없이도 실시간으로 남긴다.
    print(f"{timestamp} [{func_name}] : {log_input}", flush=True)