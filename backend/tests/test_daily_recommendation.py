"""每日荐股模块离线回归测试。"""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

import app as app_module
import daily_recommendation as dr
import scheduler

client = TestClient(app_module.app)


def _directions() -> list[dict]:
    rows = []
    for direction_index in range(5):
        stocks = []
        for stock_index in range(5):
            code = f"{direction_index + 1}{stock_index + 1:05d}"
            stocks.append({
                "rank": stock_index + 1,
                "code": code,
                "name": f"股票{direction_index + 1}-{stock_index + 1}",
                "price": 10.0 + stock_index,
                "change_pct": 1.0 + stock_index,
                "amount": 100000000.0,
                "market_cap": 10000000000.0,
                "float_market_cap": 8000000000.0,
                "industry": f"方向{direction_index + 1}",
                "pe_ttm": 20.0,
                "pb": 2.0,
                "turnover_pct": 3.0,
                "volume_ratio": 1.2,
                "score": 80.0 - stock_index,
                "score_breakdown": {},
                "report": {
                    "info_code": f"R{code}",
                    "title": f"{code}深度研报",
                    "publish_date": "2026-07-16",
                    "organization": "测试证券",
                    "rating": "买入",
                    "pdf_url": f"https://example.com/{code}.pdf",
                    "read_status": "ok",
                    "pages_read": 2,
                    "excerpt": "这是从PDF正文提取的测试内容。",
                },
            })
        rows.append({
            "rank": direction_index + 1,
            "name": f"方向{direction_index + 1}",
            "board_code": f"BK{1000 + direction_index}",
            "industry_rank": direction_index + 1,
            "change_pct": 3.0,
            "up_count": 20,
            "down_count": 5,
            "breadth": 0.8,
            "fund_flow": {"name": f"方向{direction_index + 1}", "net": 10},
            "news": [{"title": "行业催化", "source": "测试源", "time": "07-17", "url": "https://example.com"}],
            "score": 90.0 - direction_index,
            "score_breakdown": {},
            "stocks": stocks,
        })
    return rows


def _selection(directions: list[dict]) -> dict:
    stock = directions[0]["stocks"][0]
    return {
        "selected_code": stock["code"],
        "selected_name": stock["name"],
        "selected_direction": directions[0]["name"],
        "confidence": "中",
        "thesis": "综合证据相对占优。",
        "market_analysis": "市场环境测试。",
        "direction_analysis": "方向分析测试。",
        "candidate_comparison": "候选比较测试。",
        "catalysts": ["催化一"],
        "risks": ["风险一"],
        "invalidation_conditions": ["失效条件一"],
    }


def _final_reports(code: str) -> list[dict]:
    return [{
        "info_code": f"R{code}",
        "title": "最终标的深度研报",
        "publish_date": "2026-07-16",
        "organization": "测试证券",
        "researcher": "测试分析师",
        "rating": "买入",
        "industry": "测试行业",
        "predicted_eps": {"this_year": 1.0},
        "predicted_pe": {"this_year": 20.0},
        "pages": 8,
        "pdf_url": "https://example.com/report.pdf",
        "read_status": "ok",
        "pages_read": 5,
        "excerpt": "实际PDF正文摘录。",
        "read_error": None,
    }]


def test_daily_schedule_next_run():
    now = datetime(2026, 7, 17, 19, 30, tzinfo=scheduler.BEIJING)
    config = {
        "enabled": True,
        "schedule_type": "daily",
        "daily_time": "20:00",
        "last_run": None,
    }
    assert scheduler._next_run_est(config, now) == "2026-07-17 20:00:00"
    config["last_run"] = "2026-07-17 20:00:01"
    assert scheduler._next_run_est(config, now) == "2026-07-18 20:00:00"


@pytest.mark.parametrize("value, valid", [
    ("00:00", True),
    ("20:00", True),
    ("23:59", True),
    ("24:00", False),
    ("9:00", False),
    ("abc", False),
])
def test_daily_time_validation(value, valid):
    assert scheduler._valid_daily_time(value) is valid


def test_direction_news_uses_market_fallback():
    radar = {
        "macro": {
            "items": [{
                "title": "宏观消息",
                "source": "测试源",
                "time": "07-17",
                "url": "https://example.com/macro",
            }],
        },
    }
    news, score = dr._direction_news("无法映射的新行业", radar)
    assert news[0]["scope"] == "market"
    assert score > 0


def test_ai_cannot_select_outside_candidate_pool(monkeypatch):
    directions = _directions()
    monkeypatch.setattr(
        dr,
        "_call_ai",
        lambda *_args: ({"selected_code": "600519"}, None, "ai"),
    )
    selected, mode, warnings = dr._select_stock(
        {"sentiment": {"breadth": "中性", "speculation": "普通"}},
        directions,
    )
    valid_codes = {stock["code"] for direction in directions for stock in direction["stocks"]}
    assert selected["selected_code"] in valid_codes
    assert mode == "quantitative_fallback"
    assert any("候选池" in warning for warning in warnings)


def test_incomplete_ai_selection_falls_back(monkeypatch):
    directions = _directions()
    valid_code = directions[0]["stocks"][0]["code"]
    monkeypatch.setattr(
        dr,
        "_call_ai",
        lambda *_args: ({"selected_code": valid_code, "thesis": ""}, None, "ai"),
    )
    selected, mode, warnings = dr._select_stock(
        {"sentiment": {"breadth": "中性", "speculation": "普通"}},
        directions,
    )
    assert selected["market_analysis"]
    assert selected["risks"]
    assert mode == "quantitative_fallback"
    assert any("字段不完整" in warning for warning in warnings)


def test_validate_report_enforces_five_by_five_and_reports():
    directions = _directions()
    selection = _selection(directions)
    report = {
        "directions": directions,
        "recommendation": {
            "code": selection["selected_code"],
            "reports": _final_reports(selection["selected_code"]),
        },
    }
    dr._validate_report(report)
    report["directions"][0]["stocks"].pop()
    with pytest.raises(dr.RecommendationError, match="每方向5只"):
        dr._validate_report(report)


def test_generate_persists_complete_report(tmp_path, monkeypatch):
    directions = _directions()
    selection = _selection(directions)
    reports = _final_reports(selection["selected_code"])
    report_analysis = {
        "overall": "综合分析",
        "consensus": "共识",
        "differences": "分歧",
        "key_assumptions": ["假设"],
        "report_risks": ["研报风险"],
        "per_report": [{
            "info_code": reports[0]["info_code"],
            "summary": "逐篇摘要",
            "positive_factors": ["积极因素"],
            "risk_factors": ["风险因素"],
            "forecast_assumptions": ["预测假设"],
        }],
    }
    monkeypatch.setattr(dr, "REPORT_DIR", tmp_path / "daily-recommendations")
    monkeypatch.setattr(dr, "LATEST_FILE", tmp_path / "daily-recommendations" / "latest.json")
    monkeypatch.setattr(
        dr,
        "_now",
        lambda: datetime(2026, 7, 17, 20, 0, tzinfo=dr.BEIJING),
    )
    monkeypatch.setattr(dr.newsradar, "fetch_radar", lambda: {
        "generated_at": "2026-07-17 19:50",
        "industries": [{"items": [{"title": "消息"}]}],
    })
    monkeypatch.setattr(dr.market, "get_overview", lambda: {"sentiment": {}, "sectors": []})
    monkeypatch.setattr(dr, "_build_directions", lambda *_args: directions)
    monkeypatch.setattr(dr, "_market_snapshot", lambda _overview: {"sentiment": {}})
    monkeypatch.setattr(dr, "_select_stock", lambda *_args: (selection, "ai", []))
    monkeypatch.setattr(dr, "_read_final_reports", lambda _code: reports)
    monkeypatch.setattr(dr, "_analyze_final_reports", lambda *_args: (report_analysis, "ai", []))

    result = dr.generate_daily_report(force=True)

    assert len(result["directions"]) == 5
    assert all(len(direction["stocks"]) == 5 for direction in result["directions"])
    assert result["recommendation"]["code"] == selection["selected_code"]
    assert result["recommendation"]["reports"][0]["pdf_url"]
    assert result["recommendation"]["reports"][0]["analysis"]["summary"] == "逐篇摘要"
    assert result["analysis_mode"] == "ai"
    assert (tmp_path / "daily-recommendations" / "2026-07-17.json").exists()
    assert dr.get_latest()["recommendation"]["code"] == selection["selected_code"]


def test_daily_recommendation_api_contract(monkeypatch):
    payload = {"latest": {"date": "2026-07-17"}, "history": [{"date": "2026-07-17"}]}
    monkeypatch.setattr(dr, "get_dashboard", lambda _limit: payload)
    response = client.get("/api/daily-recommendation")
    assert response.status_code == 200
    assert response.json()["data"] == payload


def test_daily_task_time_api():
    response = client.put(
        "/api/tasks/daily-recommendation",
        json={"daily_time": "21:30", "enabled": True},
    )
    assert response.status_code == 200
    task = response.json()["data"]
    assert task["schedule_type"] == "daily"
    assert task["daily_time"] == "21:30"
    assert client.put(
        "/api/tasks/daily-recommendation",
        json={"daily_time": "25:00"},
    ).status_code == 400


def test_scheduled_daily_task_forces_evening_refresh(monkeypatch):
    calls = []
    monkeypatch.setattr(dr, "generate_daily_report", lambda force=False: calls.append(force))
    callback = scheduler._REGISTRY["daily-recommendation"]["fn"]
    callback()
    assert calls == [True]


def test_manual_task_failure_returns_502(monkeypatch):
    monkeypatch.setattr(scheduler, "run_once", lambda _key: (_ for _ in ()).throw(RuntimeError("boom")))
    response = client.post("/api/tasks/daily-recommendation/run")
    assert response.status_code == 502
    assert "boom" in response.json()["detail"]
