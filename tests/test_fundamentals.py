from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

from tests.fixtures import copy_synthetic_packet
from wonder_stockresearch import futu_fundamentals, research, storage
from wonder_stockresearch.models import ResearchPacket
from wonder_stockresearch.sources import SourceError


def make_root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        "[project]\nname = 'test-project'\nversion = '0'\n", encoding="utf-8"
    )
    return root


def fake_payload() -> dict[str, dict]:
    d = futu_fundamentals._dataset
    return {
        "profile": d("profile", [{"name": "公司业务", "value": "数字信息服务"}]),
        "executives": d(
            "executives",
            [{"leader_name": "柯瑞文", "position_name": "董事长", "issue_date_str": "2026-06-22"}],
            available_from="2026-06-22",
        ),
        "plates": d("plates", [{"plate_name": "电信股"}]),
        "shareholders": d(
            "shareholders",
            {"main_holder": [{"static_date_str": "2026-09-21", "holder_pct": 63.9}]},
            available_from="2026-09-21",
        ),
        "statements": d(
            "statements",
            {"statements": {"1": {"label": "利润表", "report_list": [{"date_time_str": "2025-12-31"}]}}},
            available_from="2025-12-31",
            currency="CNY",
            accounting_standard="国际会计准则",
        ),
        "operational_efficiency": d(
            "operational_efficiency",
            {"item_list": [{"end_date_str": "2025-12-31", "employee_num": 277911}]},
            available_from="2025-12-31",
        ),
        "revenue_breakdown": d(
            "revenue_breakdown",
            {"period": "2026/H1", "breakdown_list": [{"type": "PRODUCT", "item_list": []}]},
            available_from="2026-06-30",
        ),
        "dividends": d(
            "dividends",
            {"dividend_list": [{"pub_date": "2026/08/20", "statement": "中期息0.1606人民币;"}]},
            available_from="2026-08-20",
        ),
        "buybacks": d("buybacks", {"hk_buy_back_list": []}),
        "valuation": d("valuation", {"valuation_type": "PE", "trend": {"current_value": 11.8}}),
        "consensus": d("consensus", {"rating": "STRONG_BUY", "average": 6.67}),
    }


class FakeFundamentalsProvider:
    name = "futu"

    def __init__(self, data: dict[str, dict] | None = None, error: SourceError | None = None) -> None:
        self.data = data if data is not None else fake_payload()
        self.error = error
        self.closed = False

    def fetch(self, dataset: str, ticker: str) -> dict:
        if self.error:
            raise self.error
        return self.data[dataset]

    def close(self) -> None:
        self.closed = True


def test_collect_fundamentals_archives_sources_and_steps(tmp_path: Path) -> None:
    """Dropping periods/currency/evidence class from provider archives must fail this test."""
    root = make_root(tmp_path)
    run_id = str(research.start_run(root, "SYNTHETIC")["run_id"])
    provider = FakeFundamentalsProvider()

    data, issues = research.collect(
        root, run_id, "fundamentals", fundamentals_provider=provider
    )

    assert issues == []
    assert data["succeeded"] == len(research.FUNDAMENTAL_TASKS)
    assert data["failed"] == 0 and data["blocked"] == 0
    assert provider.closed is True
    listed = research.list_sources(root, run_id)["sources"]
    datasets = {source["metadata"].get("dataset") for source in listed}
    assert {"statements", "dividends", "executives"} <= datasets
    assert "valuation" not in datasets and "consensus" not in datasets
    statements = next(source for source in listed if source["metadata"].get("dataset") == "statements")
    assert statements["metadata"]["available_from"] == "2025-12-31"
    assert statements["metadata"]["evidence_class"] == "reported"
    assert statements["metadata"]["currency"] == "CNY"
    assert statements["metadata"]["accounting_standard"] == "国际会计准则"
    steps = research.get_status(root, run_id)["steps"]
    assert {step["step_key"] for step in steps} >= {key for key, _ in research.FUNDAMENTAL_TASKS}


def test_collect_fundamentals_blocked_when_provider_unavailable(tmp_path: Path) -> None:
    """Crashing instead of recording blocked steps must fail this test."""
    root = make_root(tmp_path)
    run_id = str(research.start_run(root, "SYNTHETIC")["run_id"])
    provider = FakeFundamentalsProvider(error=SourceError("opend_unavailable", "无 OpenD", blocked=True))

    data, issues = research.collect(
        root, run_id, "fundamentals", fundamentals_provider=provider
    )

    assert data["blocked"] == len(research.FUNDAMENTAL_TASKS)
    assert research.get_status(root, run_id)["source_count"] == 0
    assert any(issue.code == "collect_dataset_failed" for issue in issues)


def test_reported_period_provider_can_support_core_conclusions() -> None:
    """Ignoring a reported period's available_from must fail this test."""
    data = copy_synthetic_packet()
    data["sources"][0].update(
        {
            "kind": "provider",
            "published_at": None,
            "publication_precision": "unknown",
            "metadata": {
                "available_from": "2025-12-31",
                "observed_after_as_of": True,
                "observed_at": "2026-09-21T00:00:00+00:00",
            },
        }
    )

    assert research.validate_packet(ResearchPacket.model_validate(data)) == []


def test_period_after_as_of_provider_cannot_support() -> None:
    """Letting a period that ends after as_of support conclusions must fail this test."""
    data = copy_synthetic_packet()
    data["sources"][0].update(
        {
            "kind": "provider",
            "published_at": None,
            "publication_precision": "unknown",
            "metadata": {"available_from": "2026-12-31", "observed_at": "2026-09-21T00:00:00+00:00"},
        }
    )

    codes = {issue.code for issue in research.validate_packet(ResearchPacket.model_validate(data))}

    assert "ineligible_as_of_support" in codes


def test_writer_context_includes_provider_json(tmp_path: Path) -> None:
    """Feeding only the first 4000 chars of provider JSON (truncated) must fail this test."""
    from wonder_stockresearch import writer
    from wonder_stockresearch.models import Source

    root = make_root(tmp_path)
    run_id = str(research.start_run(root, "SYNTHETIC")["run_id"])
    research.collect(root, run_id, "fundamentals", fundamentals_provider=FakeFundamentalsProvider())
    with storage.open_db(root) as connection:
        run = storage.get_run(connection, run_id)
        rows = storage.get_run_sources(connection, run_id)
    sources = [Source.model_validate_json(row["source_json"]) for row in rows]

    _system, user = writer._build_messages(root, run, sources)

    assert "国际会计准则" in user
    assert "柯瑞文" in user


def test_revenue_breakdown_archives_ten_annual_and_three_interim_periods(
    monkeypatch,
) -> None:
    futu_module = ModuleType("futu")
    futu_module.RET_OK = 0
    monkeypatch.setitem(sys.modules, "futu", futu_module)

    screen_dates = [
        {"date": year, "period_text": f"{year}/FY", "financial_type": "F10Type_Annual"}
        for year in range(2016, 2026)
    ] + [
        {"date": year + 100, "period_text": f"{year}/H1", "financial_type": "F10Type_Q6"}
        for year in (2024, 2025, 2026)
    ] + [
        {"date": 900, "period_text": "2026/Q1", "financial_type": "F10Type_Q1"}
    ]

    class FakeContext:
        def __init__(self) -> None:
            self.requested_dates: list[int | None] = []

        def get_financials_revenue_breakdown(self, ticker: str, date: int | None = None):
            self.requested_dates.append(date)
            if date is None:
                return 0, {
                    "period": "2026/H1",
                    "currency_code": "CNY",
                    "breakdown_list": [{"type": "PRODUCT", "item_list": []}],
                    "screen_date_list": screen_dates,
                }
            selected = next(item for item in screen_dates if item["date"] == date)
            return 0, {
                "period": selected["period_text"],
                "currency_code": "CNY",
                "breakdown_list": [{"type": "PRODUCT", "item_list": [{"name": "移动业务"}]}],
            }

    provider = futu_fundamentals.FutuFundamentalsProvider()
    context = FakeContext()
    provider._context = context

    result = provider.fetch("revenue_breakdown", "HK.00728")

    payload = result["payload"]
    periods = {item["period"] for item in payload["historical_breakdowns"]}
    assert len(payload["historical_breakdowns"]) == 13
    assert "2025/FY" in periods and "2016/FY" in periods
    assert {"2024/H1", "2025/H1", "2026/H1"} <= periods
    assert "2026/Q1" not in periods
    assert len(context.requested_dates) == 13  # initial discovery + 12 historical requests
    assert result["available_from"] == "2026-06-30"
    assert result["evidence_class"] == "reported"
    assert payload["history_failures"] == []


def test_statements_follow_next_key_and_keep_at_most_twenty_unique_annual_periods(
    monkeypatch,
) -> None:
    futu_module = ModuleType("futu")
    futu_module.RET_OK = 0
    constants = ModuleType("futu.common.constant")

    class F10Type:
        ANNUAL = "ANNUAL"

    constants.F10Type = F10Type
    monkeypatch.setitem(sys.modules, "futu", futu_module)
    monkeypatch.setitem(sys.modules, "futu.common", ModuleType("futu.common"))
    monkeypatch.setitem(sys.modules, "futu.common.constant", constants)

    class FakeContext:
        def __init__(self) -> None:
            self.calls: list[tuple[int, str | None, int]] = []

        def get_financials_statements(self, ticker: str, *, statement_type: int, financial_type: object, next_key: str | None, num: int):
            self.calls.append((statement_type, next_key, num))
            year = "2025" if next_key is None else "2024"
            return 0, {
                "structure_list": [{"field_id": 5001, "display_name": "净利润"}],
                "report_list": [
                    {
                        "date_time_str": f"{year}-12-31",
                        "period_text": f"{year}/FY",
                        "currency_code": "CNY",
                        "accounting_standards": "IFRS",
                        "item_list": [{"field_id": 5001, "data": 1}],
                    }
                ],
                "next_key": "next" if next_key is None else "-1",
            }

    provider = futu_fundamentals.FutuFundamentalsProvider()
    context = FakeContext()
    provider._context = context

    result = provider.fetch("statements", "HK.00728")

    assert all(num == 50 for _statement_type, _next_key, num in context.calls)
    assert {next_key for _statement_type, next_key, _num in context.calls} == {None, "next"}
    assert all(len(block["report_list"]) == 2 for block in result["payload"]["statements"].values())
    assert result["payload"]["record_limit"] == 20
