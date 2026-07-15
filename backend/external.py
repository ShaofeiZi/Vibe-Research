"""外部数据聚合层 —— 给外部 AI 一次性拿全市场快照 + 指定股票全维度。

设计：不重写数据抓取，直接复用现有数据层函数（astock/market/newsradar/portfolio），
逐项 try/except 包裹——单只股某维度缺依赖 / 源超时 / 限流，返回 {"error": "..."}，
不阻塞其他维度、不崩整接口。外部 AI 拿到的是「尽力而为」的全量视图。

合规：与各数据层一致，只出客观公开数据，不荐股、不预测。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta

import astock
import market
import newsradar
import portfolio as pf

BEIJING = timezone(timedelta(hours=8))

MAX_CODES = 10  # 防滥用：一次最多展开 10 只股票的全维度


def _now() -> str:
    return datetime.now(BEIJING).strftime("%Y-%m-%d %H:%M:%S")


def _safe(fn, *args, **kwargs):
    """调用一个数据函数，失败返回 {"error": "..."}，不抛。DependencyMissing 单独标注。"""
    try:
        return fn(*args, **kwargs)
    except astock.DependencyMissing as e:
        return {"error": f"依赖未安装：{e}", "dependency_missing": True}
    except Exception as e:  # noqa: BLE001 — 单项失败兜底，外部 AI 据此判断该维度不可用
        return {"error": f"{type(e).__name__}: {e}"}


def _market_snapshot() -> dict:
    """全市场快照：所有不依赖具体股票的全局数据。各项独立容错。"""
    return {
        "indices": _safe(astock.index_quote),
        "global_indices": _safe(market.get_global_indices),
        "overview": _safe(market.get_overview),
        "emotion": _safe(market.get_short_term_emotion),
        "turnover_top": _safe(market.get_turnover_top),
        "industry": _safe(astock.industry_comparison, top_n=20),
        "radar": _safe(newsradar.get_radar, False),
    }


def _stock_full(code: str) -> dict:
    """单只股票全维度。各维度并行抓取，每项独立容错。"""
    dims = {
        "quote": lambda: astock.tencent_quote([code]).get(code, {}),
        "valuation": lambda: astock.full_valuation(code),
        "percentile": lambda: astock.valuation_percentile(code),
        "financials": lambda: astock.financials(code),
        "reports": lambda: astock.eastmoney_reports(code, max_pages=2),
        "news": lambda: astock.stock_news(code, limit=20),
        "announcements": lambda: astock.announcements(code, limit=15),
        "info": lambda: astock.individual_info(code),
        "kline": lambda: astock.kline(code, category=4, offset=60),
        "finance": lambda: astock.finance(code),
        "fund_flow": lambda: astock.stock_fund_flow_120d(code),
        "margin": lambda: astock.margin_trading(code),
        "block_trade": lambda: astock.block_trade(code),
        "holders": lambda: astock.holder_num_change(code),
        "dividend": lambda: astock.dividend_history(code),
        "dragon_tiger": lambda: astock.dragon_tiger_board(code),
        "lockup": lambda: astock.lockup_expiry(code),
        "blocks": lambda: astock.concept_blocks(code),
        "hot_concepts": lambda: astock.hot_concepts(code),
        "investor_qa": lambda: astock.investor_qa(code),
    }
    out = {"code": code}
    # 并行抓各维度（东财有限流，但并发上限控制在线程池默认值内；每项独立超时由源侧兜底）
    with ThreadPoolExecutor(max_workers=min(8, len(dims))) as ex:
        futures = {key: ex.submit(_safe, fn) for key, fn in dims.items()}
        for key, fut in futures.items():
            out[key] = fut.result()
    return out


def build_snapshot(codes: list[str]) -> dict:
    """构建全量快照。codes 为指定展开全维度的股票（最多 MAX_CODES 只，超出截断）。"""
    # 清洗 + 去重 + 截断
    clean = []
    for c in codes:
        c = (c or "").strip()
        if c.isdigit() and len(c) == 6 and c not in clean:
            clean.append(c)
    warnings = []
    if len(clean) > MAX_CODES:
        warnings.append(f"codes 超过 {MAX_CODES} 只上限，已截断为前 {MAX_CODES} 只")
        clean = clean[:MAX_CODES]

    snapshot = {
        "generated_at": _now(),
        "market": _market_snapshot(),
        "portfolio": _safe(pf.get_portfolio),
        "stocks": [],
    }
    if warnings:
        snapshot["warnings"] = warnings

    # 持仓股 code 也并入「已知关注标的」，供外部 AI 参考（但默认不自动展开全维度，除非在 codes 里）
    pf_data = snapshot["portfolio"]
    if isinstance(pf_data, dict) and not pf_data.get("error"):
        held = [h.get("code") for h in pf_data.get("holdings", []) if h.get("code")]
        snapshot["portfolio_codes"] = held

    if clean:
        with ThreadPoolExecutor(max_workers=min(5, len(clean))) as ex:
            snapshot["stocks"] = list(ex.map(_stock_full, clean))

    return snapshot
