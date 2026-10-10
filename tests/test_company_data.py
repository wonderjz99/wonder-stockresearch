"""虚构离线资料；验证公司投影的数值、来源版本、事务与恢复边界。"""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from tests.test_storage import make_root
from wonder_stockresearch import company_data, futu_fundamentals, storage
from wonder_stockresearch.cli import main
from wonder_stockresearch.company_data import CompanyQuery
from wonder_stockresearch.company_parser import CompanyDataError, parse_company_archive
from wonder_stockresearch.fixed import research
from wonder_stockresearch.models import Source


def archive(dataset: str, payload: object) -> bytes:
    return json.dumps(
        {"provider": "futu", "dataset": dataset, "payload": payload}, ensure_ascii=False
    ).encode()


def statements(value: object = "123456789012345678.123456789") -> dict:
    return {
        "statements": {
            "1": {
                "label": "利润表",
                "structure_list": [{"field_id": 9001, "display_name": "自定义原始科目"}],
                "report_list": [
                    {
                        "period_text": "2026/FY",
                        "date_time_str": "2026-09-02",
                        "fiscal_year": 2026,
                        "financial_type": "ANNUAL",
                        "currency_code": "USD",
                        "accounting_standards": "US GAAP",
                        "item_list": [
                            {"field_id": 9001, "data": value, "yoy": -12.5},
                        ],
                    }
                ],
            }
        }
    }


def register(root: Path, dataset: str, payload: object, ticker: str = "US.MU") -> Source:
    storage.initialize(root)
    sid = uuid4()
    raw = archive(dataset, payload)
    relative = f"data/stocks/{ticker}/sources/{sid}.json"
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    source = Source(
        id=sid,
        ticker=ticker,
        kind="provider",
        title="虚构离线测试资料",
        endpoint=f"futu_fundamentals.{dataset}",
        published_at=None,
        publication_precision="unknown",
        retrieved_at=datetime(2026, 10, 8, tzinfo=UTC),
        sha256=hashlib.sha256(raw).hexdigest(),
        raw_path=relative,
        text_path=None,
        extraction_status="available",
        metadata={"provider": "futu", "dataset": dataset},
    )
    with storage.open_db(root) as connection:
        storage.watch_stock(connection, ticker, "虚构公司", source.retrieved_at)
        storage.upsert_source(
            connection,
            {
                "id": str(sid),
                "ticker": ticker,
                "kind": "provider",
                "metadata_json": json.dumps(source.metadata),
                "sha256": source.sha256,
                "raw_path": relative,
                "text_path": None,
                "retrieved_at": storage.to_iso(source.retrieved_at),
                "source_json": source.model_dump_json(),
            },
        )
    return source


def rows(root: Path, **kwargs: object) -> list[dict]:
    result = company_data.read(root, CompanyQuery(ticker="US.MU", **kwargs))
    return [row for table in result["results"] for row in table["rows"]]


def test_statement_precision_dates_and_indicator_dimensions_are_not_guessed() -> None:
    payload = statements()
    payload["statements"]["4"] = {
        "label": "关键指标",
        "report_list": [
            {
                "period_text": "2026/FY",
                "date_time_str": "2026-09-02",
                "currency_code": "HKD",
                "item_list": [
                    {"field_id": 1, "display_name": "盈利能力TTM"},
                    {"field_id": 2, "display_name": "毛利率", "data": "80.7%"},
                ],
            }
        ],
    }
    parsed = parse_company_archive(archive("statements", payload), "statements")
    income, yoy, margin = parsed.financials
    assert income.value_decimal == "123456789012345678.123456789"
    assert income.currency == "USD" and income.unit is None and income.scale is None
    assert income.period_end == "2026-09-02" and income.period_start is None
    assert income.published_at is None and income.period_kind == "annual"
    assert income.locator.endswith(".report_list[0].item_list[0].data")
    assert yoy.value_decimal == "-12.5" and yoy.currency is None
    assert margin.currency is None and margin.unit == "%"
    assert json.loads(margin.dimensions_json)["section"] == "盈利能力TTM"


def test_efficiency_numeric_fields_not_metadata_or_invented_units() -> None:
    parsed = parse_company_archive(
        archive(
            "operational_efficiency",
            {
                "currency_code": "CNY",
                "item_list": [
                    {
                        "fiscal_year": 2025,
                        "financial_type": 7,
                        "end_date": 1767110400,
                        "end_date_str": "2025-12-31",
                        "period_text": "2025/FY",
                        "employee_num": 0,
                        "income_per_capita": -10,
                        "employee_num_yoy": "--",
                    }
                ],
            },
        ),
        "operational_efficiency",
    )
    assert [f.field_key for f in parsed.financials] == [
        "operational_efficiency:employee_num",
        "operational_efficiency:income_per_capita",
    ]
    assert parsed.financials[0].value_decimal == "0"
    assert parsed.financials[0].currency is None and parsed.financials[0].unit is None
    assert parsed.financials[1].currency == "CNY"
    assert parsed.diagnostics[0]["code"] == "invalid_numeric_value"


def test_qoq_is_preserved_as_separate_raw_attribute() -> None:
    payload = statements(100)
    payload["statements"]["1"]["report_list"][0]["item_list"][0]["qoq"] = 2
    parsed = parse_company_archive(archive("statements", payload), "statements")
    qoq = parsed.financials[-1]
    assert qoq.field_key == "statements:1:9001:qoq"
    assert qoq.currency is None and qoq.label.endswith(" / qoq")


def test_revenue_preserves_dimensions_deduplicates_exact_copies_and_keeps_conflicts() -> None:
    latest = {
        "period": "2025/FY",
        "currency_code": "USD",
        "breakdown_list": [
            {
                "type": "BUSINESS",
                "item_list": [{"name": "虚构业务", "main_oper_income": 42, "ratio": 50}],
            },
        ],
    }
    changed = copy.deepcopy(latest)
    changed["breakdown_list"][0]["item_list"][0]["main_oper_income"] = 45
    parsed = parse_company_archive(
        archive(
            "revenue_breakdown",
            {
                **latest,
                "historical_breakdowns": [latest, changed],
                "history_failures": [{"period": "2020/FY"}],
            },
        ),
        "revenue_breakdown",
    )
    assert len(parsed.financials) == 4
    income = parsed.financials[0]
    assert json.loads(income.dimensions_json) == {"category": "BUSINESS", "segment": "虚构业务"}
    assert income.currency == "USD" and parsed.financials[1].currency is None
    assert parsed.financials[2].locator.startswith("$.payload.historical_breakdowns[1]")
    assert parsed.diagnostics[0]["code"] == "archived_history_fetch_failed"


@pytest.mark.parametrize("value", [True, "NaN", "Infinity", "--", None])
def test_invalid_numbers_are_diagnostics_never_zero(value: object) -> None:
    parsed = parse_company_archive(archive("statements", statements(value)), "statements")
    assert all(f.field_key.endswith(":yoy") for f in parsed.financials)
    assert len(parsed.diagnostics) == 1


def test_empty_known_collection_differs_from_unknown_structure() -> None:
    assert parse_company_archive(archive("profile", []), "profile").profiles == []
    with pytest.raises(CompanyDataError, match="结构"):
        parse_company_archive(
            archive("operational_efficiency", {"new_shape": []}), "operational_efficiency"
        )
    with pytest.raises(CompanyDataError, match="dataset"):
        parse_company_archive(archive("profile", []), "statements")


def test_sync_is_idempotent_and_keeps_source_versions_main_database_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_root(tmp_path)
    one = register(root, "statements", statements(100))
    two = register(root, "statements", statements(120))
    register(root, "profile", [{"name": "公司名称", "value": "测试公司", "field_type": 0}])
    register(root, "statements", statements(200), "HK.00941")
    before = storage.sha256_file(storage.db_path(root))
    monkeypatch.setattr(futu_fundamentals, "make_provider", lambda: pytest.fail("network called"))
    first = company_data.sync(root)
    assert first["indexed"] == 4 and first["failed"] == 0
    second = company_data.sync(root)
    assert second["skipped"] == 4 and second["indexed"] == 0
    facts = rows(root, dataset="statements", field="自定义原始科目")
    assert {f["source_id"] for f in facts} == {str(one.id), str(two.id)}
    assert {f["value_decimal"] for f in facts} == {"100", "120"}
    assert rows(root, dataset="profile")[0]["value_json"] == '"测试公司"'
    assert storage.sha256_file(storage.db_path(root)) == before


def test_hash_failure_is_excluded_and_retry_repairs_batch(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    source = register(root, "statements", statements())
    path = root / source.raw_path
    original = path.read_bytes()
    path.write_bytes(b"{}")
    assert company_data.sync(root)["issues"][0]["code"] == "company_source_hash_mismatch"
    assert rows(root) == []
    assert company_data.status(root)["coverage"][0]["status"] == "failed"
    assert (
        "company_source_hash_mismatch"
        in company_data.status(root)["diagnostics"][0]["diagnostics_json"]
    )
    path.write_bytes(original)
    assert company_data.sync(root)["indexed"] == 1
    assert len(rows(root)) == 2


def test_source_path_and_identity_are_checked(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    source = register(root, "profile", [])
    outside = root / "data" / "outside.json"
    outside.write_bytes(archive("profile", []))
    (root / source.raw_path).unlink()
    (root / source.raw_path).symlink_to(outside)
    assert company_data.sync(root)["issues"][0]["code"] == "company_unsafe_path"
    with storage.open_db(root) as connection:
        connection.execute("UPDATE sources SET sha256=? WHERE id=?", ("0" * 64, str(source.id)))
    assert company_data.sync(root)["issues"][0]["code"] == "company_source_mismatch"


def test_company_database_symlink_cannot_replace_main_database(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    register(root, "profile", [])
    path = root / "data/company.db"
    path.symlink_to(storage.db_path(root))
    before = storage.sha256_file(storage.db_path(root))
    with pytest.raises(CompanyDataError, match="路径"):
        company_data.sync(root, rebuild=True)
    assert storage.sha256_file(storage.db_path(root)) == before


def test_crash_rolls_back_entire_batch_then_retry_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_root(tmp_path)
    register(root, "statements", statements())
    real_insert = company_data._insert_facts

    def crash(connection: sqlite3.Connection, batch_id: int, parsed: object) -> None:
        real_insert(connection, batch_id, parsed)
        raise RuntimeError("injected crash before commit")

    monkeypatch.setattr(company_data, "_insert_facts", crash)
    with pytest.raises(RuntimeError, match="injected crash"):
        company_data.sync(root)
    assert company_data.status(root)["coverage"] == []
    assert rows(root) == []
    monkeypatch.setattr(company_data, "_insert_facts", real_insert)
    assert company_data.sync(root)["indexed"] == 1


def test_rebuild_failure_preserves_previous_database_and_success_reproduces_facts(
    tmp_path: Path,
) -> None:
    root = make_root(tmp_path)
    source = register(root, "statements", statements())
    company_data.sync(root)
    before_rows = rows(root)
    before_hash = storage.sha256_file(company_data.database_path(root))
    original = (root / source.raw_path).read_bytes()
    (root / source.raw_path).write_bytes(b"bad")
    result = company_data.sync(root, rebuild=True)
    assert result["failed"] == 1 and result["activated"] is False
    assert storage.sha256_file(company_data.database_path(root)) == before_hash
    assert not list((root / "data").glob(".company-rebuild-*"))
    (root / source.raw_path).write_bytes(original)
    assert company_data.sync(root, rebuild=True)["activated"] is True
    assert rows(root) == before_rows


def test_parser_version_upgrade_retains_previous_facts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_root(tmp_path)
    register(root, "statements", statements())
    company_data.sync(root)
    monkeypatch.setattr(company_data, "PARSER_VERSION", "test-v2")
    assert company_data.sync(root)["indexed"] == 1
    assert len(rows(root)) == 2
    with sqlite3.connect(company_data.database_path(root)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM financial_facts").fetchone()[0] == 4


def test_cli_is_bounded_readonly_and_reports_partial_and_failed_batches(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = make_root(tmp_path)
    args = ["--root", str(root), "company-data"]
    assert main([*args, "status"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["initialized"] is False
    assert not storage.db_path(root).exists()
    register(root, "statements", statements("--"))
    assert main([*args, "sync"]) == 0
    data = json.loads(capsys.readouterr().out)["data"]
    assert data["partial"] == 1
    assert main([*args, "fields", "US.MU", "--limit", "1"]) == 0
    result = json.loads(capsys.readouterr().out)["data"]
    assert result["selection"] == "all_indexed_sources"
    assert main([*args, "read", "US.MU", "--limit", "999999"]) == 2
    assert json.loads(capsys.readouterr().out)["issues"][0]["code"] == "invalid_company_query"
    register(root, "operational_efficiency", {"unknown": []})
    assert main([*args, "sync"]) == 2
    assert (
        json.loads(capsys.readouterr().out)["issues"][0]["code"] == "company_structure_unsupported"
    )


def test_backup_restores_company_facts_and_source_hashes(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    source = register(root, "statements", statements())
    company_data.sync(root)
    result = research.backup(root, "data/backups/company-test")
    backup = root / "data/backups/company-test"
    manifest = json.loads((backup / "manifest.json").read_text())
    assert manifest["company_database"] == result["company_database"]
    assert storage.sha256_file(backup / "company.db") == manifest["company_database"]["sha256"]
    restored = tmp_path / "restored"
    (restored / "data").mkdir(parents=True)
    for filename in ("wonder.db", "company.db"):
        shutil.copy2(backup / filename, restored / "data" / filename)
    shutil.copytree(backup / "files/data/stocks", restored / "data/stocks")
    assert rows(restored) == rows(root)
    assert storage.sha256_file(restored / source.raw_path) == source.sha256
    assert company_data.sync(restored)["skipped"] == 1


def test_sync_respects_project_lock_and_rejects_unknown_schema(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    register(root, "profile", [])
    with sqlite3.connect(company_data.database_path(root)) as connection:
        connection.execute("PRAGMA user_version=999")
    with pytest.raises(CompanyDataError, match="schema"):
        company_data.sync(root)
    with storage.project_lock(root), pytest.raises(storage.StorageError, match="写锁"):
        company_data.sync(root, lock_timeout=0.05)
