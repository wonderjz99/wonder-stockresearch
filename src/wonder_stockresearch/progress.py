"""长任务的简短进度事件；不改变业务结果或 stdout JSON 契约。"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TextIO


@dataclass(frozen=True)
class ProgressEvent:
    """供 CLI、未来 UI 或其他宿主消费的最小进度事件。"""

    phase: str
    status: str
    message: str
    run_id: str | None = None
    session_id: str | None = None
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            object.__setattr__(self, "timestamp", datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, str | None]:
        return {
            "type": "progress",
            "phase": self.phase,
            "status": self.status,
            "message": self.message,
            "run_id": self.run_id,
            "session_id": self.session_id,
            "timestamp": self.timestamp,
        }


ProgressCallback = Callable[[ProgressEvent], None]


def emit(
    callback: ProgressCallback | None,
    phase: str,
    status: str,
    message: str,
    *,
    run_id: str | None = None,
    session_id: str | None = None,
) -> None:
    """向可选回调发送事件；没有回调时完全静默。"""

    if callback is not None:
        callback(
            ProgressEvent(
                phase=phase,
                status=status,
                message=message,
                run_id=run_id,
                session_id=session_id,
            )
        )


class StderrProgress:
    """默认的人类可读输出；只写 stderr，不污染 stdout JSON。"""

    def __init__(self, stream: TextIO | None = None) -> None:
        self.stream = stream or sys.stderr

    def __call__(self, event: ProgressEvent) -> None:
        print(f"[{event.phase}] {event.message}", file=self.stream, flush=True)
