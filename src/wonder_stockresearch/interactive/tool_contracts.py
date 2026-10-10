"""工具输入与执行范围；不加载工具目录或业务服务。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from wonder_stockresearch.models import StrictModel


@dataclass(frozen=True)
class ToolContext:
    root: Path
    row: dict[str, Any]

    @property
    def ticker(self) -> str:
        return str(self.row["ticker"])

    @property
    def run_id(self) -> str | None:
        return str(self.row["run_id"]) if self.row.get("run_id") else None

    @property
    def turn_id(self) -> str | None:
        return str(self.row["turn_id"]) if self.row.get("turn_id") else None


class EmptyArgs(StrictModel):
    pass


class SourceReadArgs(StrictModel):
    source_id: UUID
    page: int | None = None
    start_line: int | None = None
    limit_lines: int | None = None


class SearchArgs(StrictModel):
    query: str
    max_results: int = 5


class MemoReadArgs(StrictModel):
    memo_id: UUID


class FinancialQueryArgs(StrictModel):
    query: str = ""
    max_records: int = 80


class CompanyQueryArgs(StrictModel):
    dataset: (
        Literal["profile", "statements", "operational_efficiency", "revenue_breakdown"] | None
    ) = None
    query: str = ""
    period: str | None = None
    periods: list[str] = Field(default_factory=list, max_length=20)
    period_kind: Literal["annual", "interim", "quarter", "unknown"] | None = None
    field_keys: list[str] = Field(default_factory=list, max_length=16)
    offset: int = Field(default=0, ge=0, le=10000)
    mode: Literal["fields", "facts"] = "fields"
    max_records: int = Field(default=80, ge=1, le=200)


class TrustedCalculationArgs(StrictModel):
    metric: Literal["net_profit_yoy_growth", "roe_series", "capex_to_revenue", "cash_capex_proxy"]


class FetchFinancialArgs(StrictModel):
    dataset: Literal["profile", "statements", "operational_efficiency", "revenue_breakdown"]
    query: str = ""
    max_records: int = Field(default=80, ge=1, le=400)


class AnalysisDatasetArgs(StrictModel):
    query: str = ""
    max_records: int = 120


class AnalysisCodeArgs(StrictModel):
    analysis_id: UUID
    code: str


class AnalysisResultArgs(StrictModel):
    analysis_id: UUID


class ReadonlySqlArgs(StrictModel):
    sql: str


class AnalysisFunctionArgs(StrictModel):
    analysis_id: UUID
    code: str


class AnalysisArtifactArgs(StrictModel):
    analysis_id: UUID
