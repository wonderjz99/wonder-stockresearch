"""限额、可恢复的真实 Pi 题集执行器；结果必须另行逐题核验。"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import time
from pathlib import Path

from wonder_stockresearch import agent, envfile, skill_registry, storage
from wonder_stockresearch.models import InteractiveFollowup, InteractiveQuestion


def dump(path: Path, value: object) -> None:
    storage.write_atomic(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", nargs="*", help="默认全部未执行题；指定 ID 允许定向复测")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    envfile.load_env_file(root / ".env")
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise SystemExit("model_credential_not_configured")
    os.environ["WONDER_AGENT_RUNTIME"] = "pi"
    audit = root / "data/audits/20261009-pi"
    audit.mkdir(parents=True, exist_ok=True)
    isolated = audit / "isolated-root"
    ledger_file = audit / "ledger.json"
    with storage.project_lock(audit):
        if not (isolated / "ready.json").exists():
            isolated.mkdir(parents=True, exist_ok=True)
            (isolated / "data").mkdir(exist_ok=True)
            for name in ("wonder.db", "company.db"):
                original = root / "data" / name
                with (
                    sqlite3.connect(original.as_uri() + "?mode=ro", uri=True) as source,
                    sqlite3.connect(isolated / "data" / name) as destination,
                ):
                    source.backup(destination)
            shutil.copytree(root / "data/stocks", isolated / "data/stocks", dirs_exist_ok=True)
            shutil.copytree(root / "skills", isolated / "skills", dirs_exist_ok=True)
            shutil.copyfile(root / "pyproject.toml", isolated / "pyproject.toml")
            frozen = {
                str(path.relative_to(root)): storage.sha256_file(path)
                for path in (root / "data/stocks").rglob("*")
                if path.is_file() and "/runs/" in str(path)
            }
            dump(
                isolated / "ready.json",
                {
                    "original_db_sha256": storage.sha256_file(storage.db_path(root)),
                    "frozen_files": frozen,
                    "runtime": "pi-v1",
                },
            )
        ledger = (
            json.loads(ledger_file.read_text())
            if ledger_file.exists()
            else {"limit": 40, "executions": []}
        )
        cases = json.loads((root / "docs/evals/interactive-agent-30.v1.json").read_text())["cases"]
        selected = set(
            args.cases
            or [
                case["id"]
                for case in cases
                if not any(item["case_id"] == case["id"] for item in ledger["executions"])
            ]
        )
        if selected - {case["id"] for case in cases}:
            raise SystemExit("unknown_case")
        loaded = skill_registry.load_selected(
            isolated,
            ["company-analysis-method", "interactive-research-agent", "analysis-workspace"],
        )
        for case in cases:
            if case["id"] not in selected:
                continue
            if len(ledger["executions"]) >= 40:
                raise SystemExit("real_question_limit_exhausted")
            number = len(ledger["executions"]) + 1
            entry = {
                "execution": number,
                "case_id": case["id"],
                "status": "started",
                "started_at": storage.to_iso(storage.now_utc()),
                "runtime": "pi-v1",
            }
            ledger["executions"].append(entry)
            dump(ledger_file, ledger)  # 请求之前记数，崩溃也计入额度。
            start = time.monotonic()
            outcome = {"case": case, "execution": number, "quality": "pending_review"}
            print(
                json.dumps({"execution": number, "case_id": case["id"], "status": "started"}),
                flush=True,
            )
            with storage.open_db(isolated) as connection:
                before = {row[0] for row in connection.execute("SELECT id FROM agent_turns")}
            try:
                if case.get("entrypoint") == "ask-followup":
                    prior = [
                        item
                        for item in ledger["executions"]
                        if item["case_id"] == case["depends_on"] and item.get("session_id")
                    ]
                    if not prior:
                        raise agent.AgentError("dependency_missing", "依赖题没有可续问的 session")
                    result = agent.followup(
                        isolated,
                        prior[-1]["session_id"],
                        InteractiveFollowup(question=case["question"]),
                    )
                else:
                    result = agent.ask(
                        isolated,
                        case["ticker"],
                        InteractiveQuestion(
                            question=case["question"],
                            context="company_latest",
                            allow_network=case.get("allow_network", True),
                            token_budget=30000,
                            max_iterations=6,
                        ),
                        skill_context=skill_registry.execution_context(loaded),
                        skill_audit=skill_registry.selection_audit(loaded),
                        tool_names=skill_registry.execution_tool_ids(
                            loaded, "interactive_research"
                        ),
                    )
                outcome.update(status=result["status"], result=result)
            except agent.AgentError as error:
                outcome.update(
                    status="failed", error={"code": error.code, "message": error.message}
                )
            finally:
                outcome["elapsed_seconds"] = round(time.monotonic() - start, 3)
                with storage.open_db(isolated) as connection:
                    turns = [
                        dict(row)
                        for row in connection.execute("SELECT * FROM agent_turns")
                        if row["id"] not in before
                    ]
                    if turns:
                        turn = turns[-1]
                        outcome.update(
                            turn_id=turn["id"],
                            session_id=turn["session_id"],
                            used_tokens=turn["used_tokens"],
                            iterations=turn["iteration"],
                        )
                        outcome["events"] = [
                            dict(row)
                            for row in connection.execute(
                                "SELECT * FROM agent_events WHERE turn_id=? ORDER BY sequence",
                                (turn["id"],),
                            )
                        ]
                        outcome["messages"] = storage.list_agent_messages(connection, turn["id"])
                baseline = json.loads((isolated / "ready.json").read_text())
                outcome["original_unchanged"] = storage.sha256_file(
                    storage.db_path(root)
                ) == baseline["original_db_sha256"] and all(
                    storage.sha256_file(root / path) == digest
                    for path, digest in baseline["frozen_files"].items()
                )
                entry.update(
                    {
                        key: outcome[key]
                        for key in (
                            "status",
                            "session_id",
                            "turn_id",
                            "used_tokens",
                            "iterations",
                            "elapsed_seconds",
                        )
                        if key in outcome
                    }
                )
                dump(audit / f"{number:02d}-{case['id']}.json", outcome)
                dump(ledger_file, ledger)
                print(json.dumps(entry, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
