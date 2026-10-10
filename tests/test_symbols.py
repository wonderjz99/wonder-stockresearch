from __future__ import annotations

import json
from pathlib import Path

import pytest

from wonder_stockresearch import symbols
from wonder_stockresearch.sources import SourceError


def make_root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        "[project]\nname = 'test-project'\nversion = '0'\n", encoding="utf-8"
    )
    return root


class FakeSearchProvider:
    def __init__(self, records: list[dict[str, object]]) -> None:
        self.records = records
        self.closed = False

    def search_quotes(self, keyword: str, max_count: int = 10) -> list[dict[str, object]]:
        assert keyword == "保利"
        assert max_count == 10
        return self.records

    def close(self) -> None:
        self.closed = True


def test_resolve_futu_candidates_normalizes_codes_and_filters_security_types(
    tmp_path: Path,
) -> None:
    root = make_root(tmp_path)
    provider = FakeSearchProvider(
        [
            {
                "market": "HK",
                "code": "06049",
                "name": "保利物业",
                "sec_type": "STOCK",
                "is_watched": True,
            },
            {"market": "HK", "code": "00119", "name": "保利置业", "sec_type": "STOCK"},
            {"market": "HK", "code": "03800", "name": "协鑫科技", "sec_type": "STOCK"},
            {"market": "HK", "code": "01234", "name": "保利 ETF", "sec_type": "ETF"},
            {"market": "SH", "code": "600048", "name": "保利发展", "sec_type": "STOCK"},
        ]
    )

    result = symbols.resolve(root, "保利", provider_factory=lambda: provider)

    assert [item.ticker for item in result.candidates] == ["HK.06049", "HK.00119"]
    assert all(item.source == "futu" and item.verified for item in result.candidates)
    assert "is_watched" not in result.candidates[0].model_dump()
    assert provider.closed is True


def test_resolve_uses_unverified_model_candidates_when_futu_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_root(tmp_path)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(
        symbols.model_request,
        "request_deepseek",
        lambda **kwargs: {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "candidates": [
                                    {
                                        "name": "保利物业",
                                        "ticker": "HK.06049",
                                        "market": "HK",
                                        "security_type": "stock",
                                    }
                                ]
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ]
        },
    )

    def unavailable_provider() -> object:
        raise SourceError("opend_unavailable", "OpenD unavailable", blocked=True)

    result = symbols.resolve(root, "保利", provider_factory=unavailable_provider)

    assert len(result.candidates) == 1
    assert result.candidates[0].source == "model_suggestion"
    assert result.candidates[0].verified is False
    assert result.provider_error == "OpenD unavailable"
