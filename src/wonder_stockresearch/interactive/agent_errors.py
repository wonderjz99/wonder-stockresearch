"""交互 Agent 的受控错误契约。"""

from __future__ import annotations


class AgentError(Exception):
    """交互 Agent 错误；不暴露密钥、认证头或完整提示词。"""

    def __init__(
        self, code: str, message: str, *, exit_code: int = 1, consumed_tokens: int | None = None
    ) -> None:
        self.code = code
        self.message = message
        self.exit_code = exit_code
        self.consumed_tokens = consumed_tokens
        super().__init__(message)
