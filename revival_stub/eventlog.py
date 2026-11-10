"""Small structured JSON event logger with optional in-memory capture."""

from __future__ import annotations

import json
import sys
import threading
from datetime import UTC, datetime
from typing import Any, Callable, TextIO


class EventLogger:
    def __init__(
        self,
        *,
        stream: TextIO | None = sys.stdout,
        sink: Callable[[dict[str, Any]], None] | None = None,
        hex_dump: bool = False,
    ) -> None:
        self.stream = stream
        self.sink = sink
        self.hex_dump = hex_dump
        self._lock = threading.Lock()

    def emit(self, event: str, **fields: Any) -> dict[str, Any]:
        record = {
            "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "event": event,
            **fields,
        }
        with self._lock:
            if self.sink is not None:
                self.sink(record.copy())
            if self.stream is not None:
                # TASK-0349: a log line must never take the server down. On a Chinese Windows
                # console the stream is GBK, so one character outside that range (a U+FFFD from
                # a decode, an emoji in a team string) raised UnicodeEncodeError inside the
                # packet path and the client saw a dropped connection mid-fight (live session
                # 2026-10-27 00:08). Degrade the character instead of the connection.
                _line = json.dumps(record, ensure_ascii=False, sort_keys=True)
                try:
                    print(_line, file=self.stream, flush=True)
                except UnicodeEncodeError:
                    _encoding = getattr(self.stream, "encoding", None) or "utf-8"
                    print(_line.encode(_encoding, "replace").decode(_encoding, "replace"),
                          file=self.stream, flush=True)
        return record


def mask_secret(value: object, *, visible: int = 2) -> str:
    text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)
    if not text:
        return ""
    if len(text) <= visible * 2:
        return "*" * len(text)
    return f"{text[:visible]}{'*' * max(4, len(text) - visible * 2)}{text[-visible:]}"

