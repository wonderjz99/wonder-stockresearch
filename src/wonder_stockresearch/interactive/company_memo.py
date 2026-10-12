"""每家公司一个主备忘录；原 company_memos 是不可覆盖的保存条目。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any
from uuid import uuid4

from wonder_stockresearch import storage
from wonder_stockresearch.interactive.agent_errors import AgentError


def attach(
    connection: sqlite3.Connection, memo: dict[str, Any], *, report_turn_id: str | None = None
) -> None:
    """调用者持有项目写锁和事务；每个报告的快捷保存至多一次。"""
    ticker = memo["ticker"]
    connection.execute(
        """INSERT OR IGNORE INTO company_notebooks(ticker,id,title,created_at,updated_at)
        VALUES (?,?,?,?,?)""",
        (ticker, str(uuid4()), f"{ticker} 公司备忘录", memo["created_at"], memo["created_at"]),
    )
    revision = connection.execute(
        "SELECT COALESCE(MAX(revision),0)+1 FROM company_memo_links WHERE ticker=?", (ticker,)
    ).fetchone()[0]
    connection.execute(
        "INSERT INTO company_memo_links(memo_id,ticker,report_turn_id,revision) VALUES (?,?,?,?)",
        (memo["memo_id"], ticker, report_turn_id, revision),
    )
    connection.execute(
        "UPDATE company_notebooks SET updated_at=? WHERE ticker=?", (memo["created_at"], ticker)
    )


def list_for_company(connection: sqlite3.Connection, ticker: str) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in connection.execute(
            """SELECT n.id AS memo_id,n.ticker,n.title,n.created_at,n.updated_at,
            COUNT(l.memo_id) AS entry_count,COALESCE(MAX(l.revision),0) AS version
            FROM company_notebooks n LEFT JOIN company_memo_links l ON l.ticker=n.ticker
            WHERE n.ticker=? GROUP BY n.ticker""",
            (ticker,),
        )
    ]


def read(
    connection: sqlite3.Connection, identifier: str, *, version: int | None = None
) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT ticker FROM company_notebooks WHERE id=? OR ticker=?", (identifier, identifier)
    ).fetchone()
    if row is None:
        return None
    result = list_for_company(connection, row["ticker"])[0]
    if version is not None and not 1 <= version <= result["version"]:
        raise AgentError("unknown_memo_version", "该备忘录版本不存在", exit_code=2)
    entries = []
    for saved in connection.execute(
        """SELECT m.*,l.revision,l.report_turn_id FROM company_memo_links l
        JOIN company_memos m ON m.id=l.memo_id
        WHERE l.ticker=? AND l.revision<=? ORDER BY l.revision""",
        (row["ticker"], version or result["version"]),
    ):
        entry = dict(saved)
        entry["source_refs"] = json.loads(entry.pop("source_refs_json"))
        entry["tags"] = json.loads(entry.pop("tags_json"))
        entries.append(entry)
    if version is not None:
        result.update(
            version=version, entry_count=len(entries), updated_at=entries[-1]["created_at"]
        )
    result["entries"] = entries
    result["content"] = render(result)
    return result


def render(notebook: dict[str, Any]) -> str:
    lines = [
        f"# {notebook['title']}",
        "",
        f"版本：{notebook['version']}；已保存 {notebook['entry_count']} 条分析/历史记录。",
        "用户选择保存的研究记录；结论仍需核对来源，历史冲突与缺口保留。",
    ]
    for entry in notebook["entries"]:
        lines.extend(
            [
                "",
                f"## {entry['revision']}. {entry['title']}",
                "",
                f"保存时间：{entry['created_at']}；条目版本：{entry['version']}",
                f"原会话：{entry['created_from_session']}；原问题：{entry['created_from_report_turn']}",
                "",
                entry["content"],
                "",
                "来源引用：",
            ]
        )
        if not entry["source_refs"]:
            lines.append("- 本次回答没有来源引用；仅保存其原始说明与缺口。")
        for ref in entry["source_refs"]:
            lines.append(f"- {ref['source_id']} · {ref['locator']} · {ref['relation']}")
    return "\n".join(lines) + "\n"


def export(root: Path, ticker: str) -> str:
    """数据库是权威记录；导出失败时再次 save 可修复文件而不重复添加。"""
    with storage.open_db(root) as connection:
        notebook = read(connection, ticker)
    if notebook is None:
        raise AgentError("unknown_memo", "该公司尚无已保存备忘录", exit_code=2)
    path = root / "data" / "stocks" / ticker / "memo.md"
    if not path.resolve().is_relative_to(root.resolve() / "data"):
        raise AgentError(
            "unsafe_memo_path", "备忘录已保存到数据库，但导出路径不在项目 data/ 内", exit_code=2
        )
    try:
        storage.write_atomic(path, notebook["content"])
    except OSError as error:
        raise AgentError(
            "memo_export_failed", "备忘录已保存到数据库，但 Markdown 导出失败；再次 save 可修复文件"
        ) from error
    return str(path.relative_to(root))
