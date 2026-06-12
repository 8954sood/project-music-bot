from __future__ import annotations

import re

from cogs.music import _query_log_fields
from core.audio.startup_timer import PlayStartupTimer
from core.util import log_exception


def test_startup_timer_uses_one_request_id_for_all_steps(capsys):
    timer = PlayStartupTimer(123)

    timer.mark("message_received")
    timer.mark("playback_start")

    output = capsys.readouterr().out
    request_ids = re.findall(r"request_id=([a-f0-9]{12})", output)
    assert request_ids == [timer.request_id, timer.request_id]
    assert "guild_id=123" in output


def test_query_log_fields_do_not_include_raw_query():
    raw_query = "private search words"

    fields = _query_log_fields(raw_query)

    assert raw_query not in fields
    assert "query_length=20" in fields
    assert re.search(r"query_hash=[a-f0-9]{12}", fields)


def test_exception_log_includes_traceback_and_redacts_secrets(capsys):
    try:
        raise RuntimeError(
            "Authorization=Bearer abc.secret password=hunter2 "
            "url=https://example.test/play?token=sensitive&video=ok"
        )
    except RuntimeError as exc:
        log_exception("play failed request_id=abc123", exc)

    output = capsys.readouterr().out
    assert "exception_type=RuntimeError" in output
    assert "Traceback (most recent call last)" in output
    assert "request_id=abc123" in output
    assert "abc.secret" not in output
    assert "hunter2" not in output
    assert "sensitive" not in output
    assert output.count("[REDACTED]") >= 3
