from __future__ import annotations

from datetime import datetime
import sys
from typing import Any, Optional


def log_event(user_input: Optional[Any]) -> None:
    timestamp = datetime.now().isoformat(timespec="seconds")
    func_name = sys._getframe(1).f_code.co_name
    log_input = "-" if user_input is None else str(user_input)
    print(f"{timestamp} [{func_name}] : {log_input}")