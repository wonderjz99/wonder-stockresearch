"""受控的项目内 Node 子进程协议；不实现 Agent/模型工具循环。"""

from __future__ import annotations

import json
import os
import selectors
import shutil
import signal
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from wonder_stockresearch.interactive.agent_errors import AgentError
from wonder_stockresearch.interactive.pi import pi_protocol

RUNTIME_DIR = Path(__file__).resolve().parents[2] / "pi_runtime"
MAX_FRAME_BYTES = 8 * 1024 * 1024
IDLE_TIMEOUT = 330


def run(config: dict[str, Any], callback: Callable[[str, dict[str, Any]], Any]) -> None:
    config = pi_protocol.validate_config(config)
    node = shutil.which("node")
    if node is None or not (RUNTIME_DIR / "node_modules/@earendil-works/pi-agent-core").is_dir():
        raise AgentError(
            "pi_runtime_unavailable", "Pi runtime 未安装；请按 README 在项目内运行 npm ci"
        )
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"NODE_OPTIONS", "NODE_PATH", "NODE_DEBUG"}
    }
    process = subprocess.Popen(
        [node, str(RUNTIME_DIR / "runner.mjs")],
        cwd=RUNTIME_DIR,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=env,
        start_new_session=True,
    )
    assert process.stdin is not None and process.stdout is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)

    def send(value: object) -> None:
        data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n"
        if len(data) > MAX_FRAME_BYTES:
            raise AgentError("pi_frame_too_large", "Pi 协议消息超过大小限制")
        process.stdin.write(data)
        process.stdin.flush()

    buffer = b""
    deadline = time.monotonic() + IDLE_TIMEOUT
    protocol = pi_protocol.ProtocolSession()
    try:
        send(config)
        while True:
            if b"\n" not in buffer:
                if time.monotonic() >= deadline:
                    raise AgentError("pi_runtime_timeout", "Pi runtime 等待超时，已保留执行进度")
                if not selector.select(timeout=1):
                    continue
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    raise AgentError("pi_runtime_failed", "Pi runtime 提前退出，已保留执行进度")
                buffer += chunk
                if len(buffer) > MAX_FRAME_BYTES:
                    raise AgentError("pi_frame_too_large", "Pi 协议消息超过大小限制")
                continue
            line, buffer = buffer.split(b"\n", 1)
            try:
                request = json.loads(line)
            except (ValueError, UnicodeDecodeError) as error:
                raise AgentError("pi_protocol_error", "Pi runtime 返回非法协议消息") from error
            request = protocol.accept(request)
            if isinstance(request, pi_protocol.DoneFrame):
                process.stdin.close()
                if process.wait(timeout=5) != 0:
                    raise AgentError("pi_runtime_failed", "Pi runtime 退出异常")
                return
            if isinstance(request, pi_protocol.FailedFrame):
                raise AgentError("pi_runtime_failed", "Pi runtime 执行失败，已保留进度")
            # ready is a transport handshake, never a business callback. Exceptions from
            # later callbacks propagate and terminate Node rather than becoming tool errors.
            result = (
                {"protocol_version": pi_protocol.PROTOCOL_VERSION}
                if request.event == "ready"
                else callback(request.event, request.payload)
            )
            send(pi_protocol.reply(request, result))
            deadline = time.monotonic() + IDLE_TIMEOUT
    finally:
        selector.close()
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
        process.stdout.close()
        if not process.stdin.closed:
            process.stdin.close()
