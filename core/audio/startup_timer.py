from __future__ import annotations

import time
from typing import Any
from uuid import uuid4

from core.util import log_event


class PlayStartupTimer:
    def __init__(self, guild_id: int) -> None:
        self.guild_id = guild_id
        self.request_id = uuid4().hex[:12]
        self.start = time.monotonic()

    def mark(self, step: str, **fields: Any) -> None:
        elapsed_ms = (time.monotonic() - self.start) * 1000
        suffix = " ".join(f"{key}={value}" for key, value in fields.items())
        message = (
            f"play_metric request_id={self.request_id} guild_id={self.guild_id} step={step} "
            f"elapsed_ms={elapsed_ms:.1f}"
        )
        if suffix:
            message = f"{message} {suffix}"
        log_event(message)
