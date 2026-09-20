from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pypdf import PdfWriter

from tests.fixtures import copy_synthetic_packet
from wonder_stockresearch import research, sources, storage
from wonder_stockresearch.cli import main
from wonder_stockresearch.models import ResearchPacket, Source, SourceRequest
from wonder_stockresearch.research import ResearchError, validate_packet
from wonder_stockresearch.sources import SourceError


def make_root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        "[project]\nname = 'test-project'\nversion = '0'\n", encoding="utf-8"
    )
    return root


def start(root: Path) -> str:
    return str(research.start_run(root, "SYNTHETIC")["run_id"])


def test_file_import_archives_bytes_hash_and_text(tmp_path: Path) -> None:
    """Writing user-supplied hashes or skipping byte archiving must fail this test."""
    root = make_root(tmp_path)
    run_id = start(root)
    document = tmp_path / "memo.txt"
    document.write_text("中国电信公告\n第二行\n", encoding="utf-8")

    result = research.source_add(
        root,
        run_id,
        SourceRequest(kind="primary", title="教学公告", file=str(document)),
    )

    assert result["deduplicated"] is False
    assert result["extraction_status"] == "available"
    assert result["sha256"] == hashlib.sha256(document.read_bytes()).hexdigest()
    assert (root / str(result["raw_path"])).read_bytes() == document.read_bytes()
    assert "中国电信公告" in (root / str(result["text_path"])).read_text(encoding="utf-8")

    listed = research.list_sources(root, run_id)["sources"]
    assert listed[0]["origin"] == "memo.txt"
    assert listed[0]["retrieved_at"]


def test_same_bytes_reuse_file_but_keep_separate_source_records(tmp_path: Path) -> None:
    """Merging two retrieval events or rewriting duplicate bytes must fail this test."""
    root = make_root(tmp_path)
    run_id = start(root)
    document = tmp_path / "memo.txt"
    document.write_text("同一份字节\n", encoding="utf-8")
    request = SourceRequest(kind="primary", title="教学公告", file=str(document))

    first = research.source_add(root, run_id, request)
    second = research.source_add(root, run_id, request)

    assert first["source_id"] != second["source_id"]
    assert second["deduplicated"] is True
    assert first["sha256"] == second["sha256"]
    assert first["raw_path"] == second["raw_path"]
    assert len(research.list_sources(root, run_id)["sources"]) == 2


def test_pdf_without_text_reports_extraction_unavailable(tmp_path: Path) -> None:
    """Claiming scanned PDFs have text or dropping the raw file must fail this test."""
    root = make_root(tmp_path)
    run_id = start(root)
    pdf_path = tmp_path / "blank.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with pdf_path.open("wb") as handle:
        writer.write(handle)

    result = research.source_add(
        root, run_id, SourceRequest(kind="primary", title="空白 PDF", file=str(pdf_path))
    )

    assert result["extraction_status"] == "extraction_unavailable"
    assert result["text_path"] is None
    assert (root / str(result["raw_path"])).is_file()


def test_html_extraction_ignores_scripts(tmp_path: Path) -> None:
    """Running scripts or keeping script text in extracted content must fail this test."""
    root = make_root(tmp_path)
    run_id = start(root)
    html_path = tmp_path / "page.html"
    html_path.write_text(
        "<html><head><script>var secret = 1;</script><style>.x{}</style></head>"
        "<body><p>公司公告</p><p>第二段</p></body></html>",
        encoding="utf-8",
    )

    result = research.source_add(
        root, run_id, SourceRequest(kind="primary", title="网页", file=str(html_path))
    )
    text = (root / str(result["text_path"])).read_text(encoding="utf-8")

    assert "公司公告" in text and "第二段" in text
    assert "secret" not in text and ".x{}" not in text


def test_source_read_limits_lines_and_reports_next_position(tmp_path: Path) -> None:
    """Returning unlimited text or a wrong next position must fail this test."""
    root = make_root(tmp_path)
    run_id = start(root)
    document = tmp_path / "long.txt"
    document.write_text("\n".join(f"第 {number} 行" for number in range(1, 201)), encoding="utf-8")
    result = research.source_add(
        root, run_id, SourceRequest(kind="primary", title="长文", file=str(document))
    )
    source_id = str(result["source_id"])

    first = research.read_source(root, run_id, source_id)
    assert first["locator"] == "lines 1-120"
    assert first["truncated"] is True
    assert first["next"] == {"start_line": 121}

    second = research.read_source(root, run_id, source_id, start_line=121)
    assert second["truncated"] is False
    assert "第 200 行" in str(second["text"])


def test_source_read_page_uses_form_feed_boundaries(tmp_path: Path) -> None:
    """Ignoring page boundaries or page range checks must fail this test."""
    root = make_root(tmp_path)
    run_id = start(root)
    document = tmp_path / "two-pages.txt"
    document.write_text("第一页内容\f第二页内容", encoding="utf-8")
    result = research.source_add(
        root, run_id, SourceRequest(kind="primary", title="两页", file=str(document))
    )
    source_id = str(result["source_id"])

    page_two = research.read_source(root, run_id, source_id, page=2)
    assert page_two["text"] == "第二页内容"
    assert page_two["total_pages"] == 2

    with pytest.raises(ResearchError) as exc_info:
        research.read_source(root, run_id, source_id, page=3)
    assert exc_info.value.code == "page_out_of_range"


def _replace_source_ids(node: object, new_id: str) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "source_id":
                node[key] = new_id
            else:
                _replace_source_ids(value, new_id)
    elif isinstance(node, list):
        for item in node:
            _replace_source_ids(item, new_id)


def test_validate_requires_archived_bytes_for_stock_sources(tmp_path: Path) -> None:
    """Skipping archived-file checks or ignoring tampering must fail this test."""
    root = make_root(tmp_path)
    run_id = start(root)
    document = tmp_path / "evidence.txt"
    document.write_text("可核验证据\n", encoding="utf-8")
    added = research.source_add(
        root,
        run_id,
        SourceRequest(
            kind="primary",
            title="证据",
            file=str(document),
            published_at=datetime(2026, 9, 1, tzinfo=UTC),
            publication_precision="datetime",
        ),
    )

    with storage.open_db(root) as connection:
        row = storage.get_source(connection, str(added["source_id"]))
    assert row is not None
    archived = Source.model_validate_json(row["source_json"])

    data = copy_synthetic_packet()
    _replace_source_ids(data["draft"], str(archived.id))
    data["sources"] = [json.loads(archived.model_dump_json())]
    research.save_draft(root, run_id, ResearchPacket.model_validate(data))
    assert research.validate_run(root, run_id) == []

    (root / archived.raw_path).unlink()
    codes = {issue.code for issue in research.validate_run(root, run_id)}
    assert "missing_archived_file" in codes

    with pytest.raises(ResearchError):
        research.finish_run(root, run_id)


def test_future_source_is_rejected_by_validation() -> None:
    """Allowing a source published after as_of to support the draft must fail this test."""
    data = copy_synthetic_packet()
    data["sources"][0]["published_at"] = "2030-01-01T00:00:00+00:00"
    packet = ResearchPacket.model_validate(data)

    codes = {issue.code for issue in validate_packet(packet)}

    assert "future_source" in codes


def test_url_request_rejects_private_and_oversized(tmp_path: Path) -> None:
    """Allowing SSRF targets or unbounded downloads must fail this test."""
    with pytest.raises(SourceError) as blocked:
        sources.fetch_url("http://127.0.0.1/secret")
    assert blocked.value.code == "blocked_host"
    assert blocked.value.exit_code == 2

    root = make_root(tmp_path)
    run_id = start(root)
    big = tmp_path / "big.txt"
    big.write_bytes(b"x" * 100)
    original = sources.MAX_BYTES
    sources.MAX_BYTES = 10
    try:
        with pytest.raises(SourceError) as too_large:
            research.source_add(
                root, run_id, SourceRequest(kind="primary", title="过大", file=str(big))
            )
    finally:
        sources.MAX_BYTES = original
    assert too_large.value.code == "file_too_large"


class _FakeResponse:
    def __init__(self, status_code: int, headers: dict[str, str], body: bytes) -> None:
        self.status_code = status_code
        self.headers = headers
        self._body = body

    def iter_content(self, size: int) -> Iterator[bytes]:
        for index in range(0, len(self._body), size):
            yield self._body[index : index + size]

    def close(self) -> None:
        return None


def test_fetch_url_follows_redirect_with_fake_transport(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Skipping redirect handling or content-type extension mapping must fail here."""
    monkeypatch.setattr(sources, "_is_public_host", lambda host: True)
    seen: list[str] = []

    def fake_get(url: str, **kwargs: object) -> _FakeResponse:
        seen.append(url)
        if url.endswith("/start"):
            return _FakeResponse(302, {"Location": "/final"}, b"")
        return _FakeResponse(200, {"Content-Type": "text/html"}, b"<p>ok</p>")

    monkeypatch.setattr(sources.requests, "get", fake_get)

    acquired = sources.fetch_url("https://example.com/start")

    assert seen == ["https://example.com/start", "https://example.com/final"]
    assert acquired.extension == "html"
    assert acquired.data == b"<p>ok</p>"
    assert acquired.final_url == "https://example.com/final"


def test_cli_source_add_sources_read(tmp_path: Path, capsys: object) -> None:
    """Breaking the source CLI contract must fail this test."""
    root = make_root(tmp_path)
    document = tmp_path / "doc.txt"
    document.write_text("正文第一行\n正文第二行\n", encoding="utf-8")
    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps({"kind": "primary", "title": "教学文档", "file": str(document)}),
        encoding="utf-8",
    )

    assert main(["--root", str(root), "start", "SYNTHETIC"]) == 0
    run_id = json.loads(capsys.readouterr().out)["data"]["run_id"]

    assert main(["--root", str(root), "source-add", run_id, "--input", str(request_path)]) == 0
    source_id = json.loads(capsys.readouterr().out)["data"]["source_id"]

    assert main(["--root", str(root), "sources", run_id]) == 0
    assert len(json.loads(capsys.readouterr().out)["data"]["sources"]) == 1

    assert main(["--root", str(root), "source-read", run_id, source_id]) == 0
    snippet = json.loads(capsys.readouterr().out)["data"]
    assert snippet["locator"] == "lines 1-2"
    assert "正文第一行" in snippet["text"]
