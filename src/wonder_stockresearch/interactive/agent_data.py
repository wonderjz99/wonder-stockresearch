"""交互问题按需补取只读财务资料；归档范围属于 Turn，不挂到正式 run。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from wonder_stockresearch import futu_fundamentals, storage
from wonder_stockresearch.fixed import research
from wonder_stockresearch.interactive import agent_company, analysis_workspace
from wonder_stockresearch.models import Source
from wonder_stockresearch.sources import SourceError


def fetch_financial_data(
    root: Path,
    row: dict[str, Any],
    dataset: str,
    query: str,
    *,
    limit: int,
) -> dict[str, Any]:
    session_id, turn_id = str(row["id"]), row.get("turn_id")
    if not turn_id:
        raise analysis_workspace.AnalysisWorkspaceError(
            "missing_agent_turn", "补取资料需要当前 Turn"
        )
    with storage.open_db(root) as connection:
        turn = storage.get_agent_turn(connection, str(turn_id))
        if turn is None or turn["session_id"] != session_id or turn["ticker"] != row["ticker"]:
            raise analysis_workspace.AnalysisWorkspaceError(
                "unknown_agent_turn", "补取资料不属于当前 Turn"
            )
        if not json.loads(turn["question_json"]).get("allow_network", True):
            raise analysis_workspace.AnalysisWorkspaceError(
                "network_disabled", "当前问题设为离线，不能补取富途数据"
            )
        existing = [
            item
            for item in storage.agent_context_sources(
                connection,
                str(row["ticker"]),
                None,
                session_id,
                str(turn_id),
            )
            if storage.is_agent_turn_source(item, session_id, str(turn_id))
            and json.loads(item["metadata_json"]).get("dataset") == dataset
        ]
    if dataset not in {"profile", "statements", "operational_efficiency", "revenue_breakdown"}:
        raise analysis_workspace.AnalysisWorkspaceError("unknown_dataset", "不支持该财务数据集")
    cached = bool(existing)
    if cached:
        source = Source.model_validate_json(existing[0]["source_json"])
    else:
        provider = futu_fundamentals.make_provider()
        try:
            payload = provider.fetch(dataset, str(row["ticker"]))
        except SourceError as error:
            raise analysis_workspace.AnalysisWorkspaceError(error.code, str(error)) from error
        finally:
            provider.close()
        payload = {**payload, "provider": "futu", "dataset": dataset}
        source_id = uuid4()
        path = (
            root
            / "data"
            / "stocks"
            / str(row["ticker"])
            / "agent-sources"
            / session_id
            / str(turn_id)
            / f"{source_id}.json"
        )
        rendered = json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n"
        source = Source(
            id=source_id,
            ticker=str(row["ticker"]),
            kind="provider",
            title=f"富途按需财务资料：{dataset}",
            endpoint=f"futu:{dataset}",
            publication_precision="unknown",
            retrieved_at=storage.now_utc(),
            sha256=hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
            raw_path=storage.relative_path(root, path),
            text_path=storage.relative_path(root, path),
            extraction_status="available",
            metadata={
                "provider": "futu",
                "dataset": dataset,
                "agent_session_id": session_id,
                "agent_turn_id": str(turn_id),
                "evidence_class": payload.get("evidence_class"),
                "available_from": payload.get("available_from"),
                "currency": payload.get("currency"),
                "accounting_standard": payload.get("accounting_standard"),
            },
        )
        storage.write_atomic(path, rendered)
        with storage.project_lock(root), storage.open_db(root) as connection:
            storage.upsert_source(connection, research._source_row(source))
    with storage.open_db(root) as connection:
        company_scope = agent_company.scope(connection, row)
    result = (
        agent_company.query(
            root,
            row,
            dataset=dataset,
            query=query,
            period=None,
            mode="facts",
            limit=min(limit, 200),
        )
        if company_scope is not None
        else analysis_workspace.archived_financials(root, row, query, limit=limit)
    )
    # 把补取本身与检索结果分开：没有命中关键词不代表接口没有返回其他字段。
    result.update(
        {
            "dataset": dataset,
            "fetched_source_id": str(source.id),
            "cached": cached,
            "retrieved_at": storage.to_iso(source.retrieved_at),
            "available_from": source.metadata.get("available_from"),
            "message": "补取来源已归档；SQL 快照可查询，检索无命中时可更换关键词。",
        }
    )
    return result
