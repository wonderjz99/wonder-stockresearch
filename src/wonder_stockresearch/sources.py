"""阶段 3 的来源获取、字节归档与文本提取；不写数据库。"""

from __future__ import annotations

import ipaddress
import logging
import socket
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from typing import ClassVar
from urllib.parse import urljoin, urlparse

import requests
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from .models import SourceRequest

MAX_BYTES = 50 * 1024 * 1024
CONNECT_TIMEOUT_SECONDS = 10
READ_TIMEOUT_SECONDS = 90
MAX_ATTEMPTS = 2
MAX_REDIRECTS = 5
MAX_RETRY_AFTER_SECONDS = 60
RETRY_STATUS = {429, 500, 502, 503, 504}
USER_AGENT = "wonder-stockresearch/0.1 (personal research; contact via project owner)"

_CONTENT_TYPE_EXTENSIONS = {
    "application/pdf": "pdf",
    "application/xhtml+xml": "html",
    "text/html": "html",
    "text/plain": "txt",
    "text/csv": "csv",
    "application/json": "json",
    "text/markdown": "md",
}
_TEXT_LIKE_EXTENSIONS = {"txt", "csv", "json", "md", "log", "text"}


class SourceError(Exception):
    """获取或 provider 错误，带稳定错误码与退出码供 CLI 输出。"""

    def __init__(
        self, code: str, message: str, *, exit_code: int = 1, blocked: bool = False
    ) -> None:
        self.code = code
        self.message = message
        self.exit_code = exit_code
        self.blocked = blocked
        super().__init__(message)


@dataclass(frozen=True)
class AcquiredSource:
    data: bytes
    extension: str
    content_type: str | None
    final_url: str | None
    origin_label: str | None


def _is_public_host(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as error:
        raise SourceError("dns_error", f"无法解析主机：{host}") from error
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        ):
            return False
    return True


def _retry_delay(response: requests.Response) -> float:
    header = response.headers.get("Retry-After")
    if header is None:
        return 1.0
    try:
        seconds = float(header)
    except ValueError:
        return 1.0
    return max(0.0, min(seconds, MAX_RETRY_AFTER_SECONDS))


def _get_with_retries(url: str, user_agent: str = USER_AGENT) -> requests.Response:
    attempt = 0
    while True:
        attempt += 1
        try:
            response = requests.get(
                url,
                stream=True,
                timeout=(CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS),
                allow_redirects=False,
                headers={"User-Agent": user_agent},
            )
        except requests.RequestException as error:
            if attempt >= MAX_ATTEMPTS:
                raise SourceError("network_error", f"网络请求失败：{error}") from error
            time.sleep(1.0)
            continue
        if response.status_code in RETRY_STATUS and attempt < MAX_ATTEMPTS:
            delay = _retry_delay(response)
            response.close()
            time.sleep(delay)
            continue
        return response


def _read_limited(response: requests.Response) -> bytes:
    chunks: list[bytes] = []
    total = 0
    try:
        for chunk in response.iter_content(65536):
            total += len(chunk)
            if total > MAX_BYTES:
                raise SourceError("file_too_large", f"响应超过上限 {MAX_BYTES} 字节", exit_code=2)
            chunks.append(chunk)
    finally:
        response.close()
    return b"".join(chunks)


def fetch_url(url: str, *, user_agent: str = USER_AGENT) -> AcquiredSource:
    """只接受 HTTP(S)，逐跳检查重定向目标，拒绝回环/私网地址。"""
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        parsed = urlparse(current)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise SourceError("invalid_url", f"只接受 HTTP(S) URL：{current}", exit_code=2)
        if not _is_public_host(parsed.hostname):
            raise SourceError("blocked_host", f"拒绝访问回环/私网地址：{parsed.hostname}", exit_code=2)
        response = _get_with_retries(current, user_agent)
        try:
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("Location")
                if not location:
                    raise SourceError("bad_redirect", "重定向缺少 Location")
                current = urljoin(current, location)
                continue
            if response.status_code >= 400:
                raise SourceError(
                    "http_error", f"HTTP {response.status_code}：{current}"
                )
            content_type = response.headers.get("Content-Type")
            data = _read_limited(response)
        finally:
            response.close()
        extension = _extension_for(content_type, current, default="bin")
        return AcquiredSource(
            data=data,
            extension=extension,
            content_type=content_type,
            final_url=current,
            origin_label=None,
        )
    raise SourceError("too_many_redirects", f"重定向超过 {MAX_REDIRECTS} 次")


def load_file(path_text: str) -> AcquiredSource:
    path = Path(path_text).expanduser()
    if not path.is_file():
        raise SourceError("file_not_found", f"找不到待导入文件：{path}")
    if path.stat().st_size > MAX_BYTES:
        raise SourceError("file_too_large", f"文件超过上限 {MAX_BYTES} 字节", exit_code=2)
    data = path.read_bytes()
    if len(data) > MAX_BYTES:
        raise SourceError("file_too_large", f"文件超过上限 {MAX_BYTES} 字节", exit_code=2)
    extension = _extension_for(None, str(path), default="bin")
    return AcquiredSource(
        data=data,
        extension=extension,
        content_type=None,
        final_url=None,
        origin_label=path.name,
    )


def acquire(request: SourceRequest) -> AcquiredSource:
    if request.url is not None:
        return fetch_url(request.url)
    assert request.file is not None
    return load_file(request.file)


def _extension_for(content_type: str | None, location: str, default: str) -> str:
    if content_type:
        media_type = content_type.split(";")[0].strip().lower()
        if media_type in _CONTENT_TYPE_EXTENSIONS:
            return _CONTENT_TYPE_EXTENSIONS[media_type]
    suffix = Path(urlparse(location).path).suffix.lower().lstrip(".")
    if suffix and 1 <= len(suffix) <= 5 and suffix.isalnum():
        return suffix
    return default


def _charset(content_type: str | None) -> str:
    if content_type:
        for part in content_type.split(";")[1:]:
            key, _, value = part.strip().partition("=")
            if key.lower() == "charset" and value:
                return value.strip("\"'")
    return "utf-8"


def _decode(data: bytes, content_type: str | None) -> str:
    return data.decode(_charset(content_type), errors="replace")


class _HtmlTextExtractor(HTMLParser):
    """只取可见文本；不执行脚本，不解析样式。"""

    _BLOCK_TAGS: ClassVar[set[str]] = {
        "p", "div", "br", "li", "tr", "td", "th", "section", "article",
        "h1", "h2", "h3", "h4", "h5", "h6", "table", "ul", "ol", "pre",
    }
    _SKIP_TAGS: ClassVar[set[str]] = {"script", "style", "noscript", "template"}

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
        elif tag in self._BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag in self._BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._parts.append(data)

    def text(self) -> str:
        raw = "".join(self._parts)
        lines = [line.strip() for line in raw.splitlines()]
        collapsed: list[str] = []
        for line in lines:
            if line or (collapsed and collapsed[-1]):
                collapsed.append(line)
        return "\n".join(collapsed).strip()


def _extract_pdf(data: bytes) -> tuple[str, str]:
    # pypdf 对缺失的字体/编码会打大量 WARNING；这些不影响文本提取，降到 ERROR。
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    try:
        reader = PdfReader(BytesIO(data))
        pages = [(page.extract_text() or "") for page in reader.pages]
    except (PdfReadError, OSError, ValueError, TypeError, KeyError, AttributeError):
        return "", "extraction_unavailable"
    text = "\f".join(pages)
    if not text.strip():
        return "", "extraction_unavailable"
    return text, "available"


def extract_text(data: bytes, extension: str, content_type: str | None) -> tuple[str, str]:
    """返回 (文本, extraction_status)。无法可靠提取时保留原文并标状态。"""
    lowered = extension.lower()
    if lowered == "pdf":
        return _extract_pdf(data)
    if lowered in {"html", "htm", "xhtml"}:
        parser = _HtmlTextExtractor()
        parser.feed(_decode(data, content_type))
        text = parser.text()
        return (text, "available") if text else ("", "extraction_unavailable")
    if lowered in _TEXT_LIKE_EXTENSIONS:
        return _decode(data, content_type), "available"
    try:
        return data.decode("utf-8"), "available"
    except UnicodeDecodeError:
        return "", "extraction_unavailable"
