from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from wonder_stockresearch import disclosures, market, sources
from wonder_stockresearch.cli import main
from wonder_stockresearch.disclosures import FilingCandidate
from wonder_stockresearch.fixed import research
from wonder_stockresearch.sources import AcquiredSource, SourceError


def make_root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        "[project]\nname = 'test-project'\nversion = '0'\n", encoding="utf-8"
    )
    return root


def start(root: Path, ticker: str = "HK.00728") -> str:
    return str(research.start_run(root, ticker)["run_id"])


class FakeMarketProvider:
    name = "futu"

    def __init__(self, error: SourceError | None = None) -> None:
        self.error = error
        self.closed = False

    def market_snapshot(self, ticker: str) -> dict[str, object]:
        if self.error:
            raise self.error
        return {
            "code": ticker,
            "endpoint": "OpenQuoteContext.get_market_snapshot",
            "records": [{"last_price": 5.12, "currency": "HKD", "volume": 12345678}],
        }

    def financial_statements(self, ticker: str) -> dict[str, object]:
        if self.error:
            raise self.error
        return {
            "code": ticker,
            "endpoint": "OpenQuoteContext.get_financials_statements",
            "financial_type": "ANNUAL",
            "record_limit": 10,
            "unit_note": "futu 未提供 item 级单位，保留原始 data 值，不推断单位",
            "statements": {
                "1": {
                    "label": "利润表",
                    "next_key": None,
                    "structure_list": [{"field_id": 5001, "display_name": "营业总收入"}],
                    "report_list": [
                        {
                            "date_time_str": "2025-12-31",
                            "fiscal_year": 2025,
                            "financial_type": "ANNUAL",
                            "period_text": "2025/FY",
                            "currency_info": "人民币",
                            "currency_code": "CNY",
                            "accounting_standards": "国际会计准则",
                            "item_list": [
                                {
                                    "field_id": 5001,
                                    "display_name": "营业总收入",
                                    "data": 529559000000.0,
                                }
                            ],
                        }
                    ],
                }
            },
        }

    def close(self) -> None:
        self.closed = True


class FakeDisclosureProvider:
    name = "hkex"

    def __init__(
        self,
        candidates: list[FilingCandidate] | None = None,
        error: SourceError | None = None,
    ) -> None:
        self.candidates = candidates or []
        self.error = error

    def list_filings(self, ticker: str, years: int = 3) -> list[FilingCandidate]:
        if self.error:
            raise self.error
        return list(self.candidates)


def candidate(title: str, url: str, published_at: datetime, category: str) -> FilingCandidate:
    return FilingCandidate(
        title=title,
        url=url,
        published_at=published_at,
        category=category,
        file_type="pdf",
        timezone_name="Asia/Hong_Kong",
    )


def test_classify_title_requires_a_periodic_report_title_not_a_mention() -> None:
    assert disclosures.classify_title("Annual Report 2021") == "annual"
    assert disclosures.classify_title("2021 Annual Report") == "annual"
    assert disclosures.classify_title("中期报告 2025") == "interim"
    assert (
        disclosures.classify_title(
            "(1) RESIGNATION OF EXECUTIVE DIRECTOR; UPDATES ON REPORT OF DIRECTORS IN 2021 ANNUAL REPORT"
        )
        == "other"
    )
    assert disclosures.classify_title("Announcement of Annual Results for 2025") == "results"


def filings_candidates() -> list[FilingCandidate]:
    return [
        candidate(
            "ANNUAL REPORT 2025",
            "https://hk.example/a2025.pdf",
            datetime(2026, 4, 1, tzinfo=UTC),
            "annual",
        ),
        candidate(
            "ANNUAL REPORT 2024",
            "https://hk.example/a2024.pdf",
            datetime(2025, 4, 1, tzinfo=UTC),
            "annual",
        ),
        candidate(
            "ANNUAL REPORT 2023",
            "https://hk.example/a2023.pdf",
            datetime(2024, 4, 1, tzinfo=UTC),
            "annual",
        ),
        candidate(
            "ANNUAL REPORT 2022",
            "https://hk.example/a2022.pdf",
            datetime(2023, 4, 1, tzinfo=UTC),
            "annual",
        ),
        candidate(
            "ANNUAL REPORT 2021",
            "https://hk.example/a2021.pdf",
            datetime(2022, 4, 1, tzinfo=UTC),
            "annual",
        ),
        candidate(
            "ANNUAL REPORT 2020",
            "https://hk.example/a2020.pdf",
            datetime(2021, 4, 1, tzinfo=UTC),
            "annual",
        ),
        candidate(
            "INTERIM REPORT 2026",
            "https://hk.example/i2026.pdf",
            datetime(2026, 8, 20, tzinfo=UTC),
            "interim",
        ),
        candidate(
            "INTERIM REPORT 2025",
            "https://hk.example/i2025.pdf",
            datetime(2025, 8, 20, tzinfo=UTC),
            "interim",
        ),
        candidate(
            "INTERIM REPORT 2024",
            "https://hk.example/i2024.pdf",
            datetime(2024, 8, 20, tzinfo=UTC),
            "interim",
        ),
        candidate(
            "INTERIM REPORT 2023",
            "https://hk.example/i2023.pdf",
            datetime(2023, 8, 20, tzinfo=UTC),
            "interim",
        ),
        candidate(
            "MONTHLY RETURN", "https://hk.example/m.pdf", datetime(2026, 9, 1, tzinfo=UTC), "other"
        ),
    ]


def fake_fetch(
    failing: set[str] | None = None,
) -> Callable[..., AcquiredSource]:
    failing = failing or set()

    def _fetch(url: str, *, user_agent: str | None = None) -> AcquiredSource:
        if url in failing:
            raise SourceError("network_error", f"模拟下载失败：{url}")
        return AcquiredSource(
            data=f"<html><body>filing {url}</body></html>".encode(),
            extension="html",
            content_type="text/html",
            final_url=url,
            origin_label=None,
        )

    return _fetch


def test_collect_market_archives_provider_sources_and_steps(tmp_path: Path) -> None:
    """Dropping provider archiving or step recording must fail this test."""
    root = make_root(tmp_path)
    run_id = start(root)
    provider = FakeMarketProvider()

    data, issues = research.collect(root, run_id, "market", market_provider=provider)

    assert issues == []
    assert data["succeeded"] == 1 and data["failed"] == 0 and data["blocked"] == 0
    assert provider.closed is True
    listed = research.list_sources(root, run_id)["sources"]
    assert len(listed) == 1
    assert all(source["kind"] == "provider" for source in listed)
    assert all(source["metadata"].get("observed_at") for source in listed)
    steps = research.get_status(root, run_id)["steps"]
    assert {step["step_key"] for step in steps} == {"market_snapshot"}
    assert {step["status"] for step in steps} == {"succeeded"}
    assert all(step["finished_at"] for step in steps)


def test_collect_market_does_not_duplicate_fundamentals_financial_statements(
    tmp_path: Path,
) -> None:
    """标准 market collect 不能再次调用或归档已由 fundamentals 负责的财务接口。"""
    root = make_root(tmp_path)
    run_id = start(root)

    research.collect(root, run_id, "market", market_provider=FakeMarketProvider())

    listed = research.list_sources(root, run_id)["sources"]
    assert [source["metadata"].get("payload_key") for source in listed] == ["market_snapshot"]


def test_collect_provider_failure_is_recorded_not_empty_success(tmp_path: Path) -> None:
    """Turning a provider failure into a silent empty result must fail this test."""
    root = make_root(tmp_path)
    run_id = start(root)
    provider = FakeMarketProvider(error=SourceError("network_error", "读取超时"))

    data, issues = research.collect(root, run_id, "market", market_provider=provider)

    assert data["succeeded"] == 0 and data["failed"] == 1
    assert {issue.code for issue in issues} == {"collect_failed"}
    assert all(issue.severity == "error" for issue in issues)
    assert research.get_status(root, run_id)["source_count"] == 0
    assert {step["status"] for step in data["steps"]} == {"failed"}


def test_collect_blocked_provider_is_warning_not_failure(tmp_path: Path) -> None:
    """Treating an unavailable provider as generic failure must fail this test."""
    root = make_root(tmp_path)
    run_id = start(root)
    provider = FakeMarketProvider(
        error=SourceError("opend_unavailable", "无法连接 OpenD", blocked=True)
    )

    data, issues = research.collect(root, run_id, "market", market_provider=provider)

    assert data["blocked"] == 1 and data["failed"] == 0
    assert all(issue.severity == "warning" for issue in issues)
    assert {step["status"] for step in data["steps"]} == {"blocked"}


def test_collect_filings_selects_latest_reports_and_downloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Downloading the wrong filings or skipping the index must fail this test."""
    root = make_root(tmp_path)
    run_id = start(root)
    monkeypatch.setattr(sources, "fetch_url", fake_fetch())

    data, issues = research.collect(
        root, run_id, "filings", disclosure_provider=FakeDisclosureProvider(filings_candidates())
    )

    assert issues == []
    step = data["steps"][0]
    assert step["status"] == "succeeded"
    assert step["candidate_count"] == 11
    assert step["selected_count"] == 8
    assert len(step["downloaded"]) == 8
    listed = research.list_sources(root, run_id)["sources"]
    assert len(listed) == 9
    assert any(source["kind"] == "secondary" for source in listed)
    titles = {source["title"] for source in listed if source["kind"] == "primary"}
    assert {
        "ANNUAL REPORT 2025",
        "ANNUAL REPORT 2024",
        "ANNUAL REPORT 2023",
        "ANNUAL REPORT 2022",
        "ANNUAL REPORT 2021",
        "INTERIM REPORT 2026",
        "INTERIM REPORT 2025",
        "INTERIM REPORT 2024",
    } <= titles
    assert "ANNUAL REPORT 2020" not in titles
    assert "INTERIM REPORT 2023" not in titles


def test_collect_filings_partial_download_failure_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hiding download failures or failing the whole step must fail this test."""
    root = make_root(tmp_path)
    run_id = start(root)
    monkeypatch.setattr(sources, "fetch_url", fake_fetch({"https://hk.example/a2025.pdf"}))

    data, issues = research.collect(
        root, run_id, "filings", disclosure_provider=FakeDisclosureProvider(filings_candidates())
    )

    step = data["steps"][0]
    assert step["status"] == "succeeded"
    assert len(step["download_failures"]) == 1
    assert step["download_failures"][0]["url"] == "https://hk.example/a2025.pdf"
    assert "collect_download_failed" in {issue.code for issue in issues}


def test_collect_filings_all_downloads_failing_marks_step_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Marking a total download failure as success must fail this test."""
    root = make_root(tmp_path)
    run_id = start(root)
    selected_urls = {
        "https://hk.example/a2025.pdf",
        "https://hk.example/a2024.pdf",
        "https://hk.example/a2023.pdf",
        "https://hk.example/a2022.pdf",
        "https://hk.example/a2021.pdf",
        "https://hk.example/i2026.pdf",
        "https://hk.example/i2025.pdf",
        "https://hk.example/i2024.pdf",
    }
    monkeypatch.setattr(sources, "fetch_url", fake_fetch(selected_urls))

    data, issues = research.collect(
        root, run_id, "filings", disclosure_provider=FakeDisclosureProvider(filings_candidates())
    )

    step = data["steps"][0]
    assert step["status"] == "failed"
    assert data["failed"] == 1
    codes = {issue.code for issue in issues}
    assert "collect_failed" in codes
    assert "collect_download_failed" in codes


def test_collect_filings_empty_result_is_recorded_distinctly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Treating an empty discovery as an error or as nothing must fail this test."""
    root = make_root(tmp_path)
    run_id = start(root)
    monkeypatch.setattr(sources, "fetch_url", fake_fetch())

    data, issues = research.collect(
        root, run_id, "filings", disclosure_provider=FakeDisclosureProvider([])
    )

    step = data["steps"][0]
    assert step["status"] == "succeeded"
    assert step["candidate_count"] == 0
    assert issues == []
    assert research.get_status(root, run_id)["source_count"] == 1


def test_sec_provider_requires_configured_user_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Silently calling SEC without a contact User-Agent must fail this test."""
    monkeypatch.delenv("WONDER_SEC_USER_AGENT", raising=False)

    with pytest.raises(SourceError) as exc_info:
        disclosures.SecDisclosureProvider(user_agent=None)
    assert exc_info.value.code == "sec_user_agent_missing"
    assert exc_info.value.blocked is True


def test_disclosure_provider_routing_by_market(monkeypatch: pytest.MonkeyPatch) -> None:
    """Routing HK/US to the wrong official channel must fail this test."""
    assert isinstance(disclosures.make_provider("HK.00728"), disclosures.HkexDisclosureProvider)
    monkeypatch.setenv("WONDER_SEC_USER_AGENT", "wonder-stockresearch test@example.invalid")
    assert isinstance(disclosures.make_provider("US.AAPL"), disclosures.SecDisclosureProvider)


def test_cli_collect_market_uses_provider_factory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: object
) -> None:
    """Breaking the collect CLI wiring must fail this test."""
    root = make_root(tmp_path)
    monkeypatch.setattr(market, "make_provider", lambda *args, **kwargs: FakeMarketProvider())

    assert main(["--root", str(root), "start", "HK.00728"]) == 0
    run_id = json.loads(capsys.readouterr().out)["data"]["run_id"]
    assert main(["--root", str(root), "collect", run_id, "--kind", "market"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["data"]["succeeded"] == 1
    assert research.get_status(root, run_id)["source_count"] == 1
