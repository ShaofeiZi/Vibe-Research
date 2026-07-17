"""每日荐股研究服务。

按北京时间晚间任务生成五个方向、每方向五只候选，并基于行情、资讯和研报正文
产出唯一的次日观察标的。报告存放在用户数据目录，不写入仓库。
"""

from __future__ import annotations

import io
import json
import math
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import requests

import aiconfig
import astock
import chat
import cli_runtime
import market
import newsradar

BEIJING = timezone(timedelta(hours=8))
DATA_DIR = Path(os.environ.get("VR_DATA_DIR") or Path.home() / ".vibe-research")
REPORT_DIR = DATA_DIR / "daily-recommendations"
LATEST_FILE = REPORT_DIR / "latest.json"
_GENERATE_LOCK = threading.Lock()
_PDF_MAX_BYTES = 12 * 1024 * 1024
_PDF_MAX_PAGES = 8
_CANDIDATE_REPORT_CHARS = 900
_FINAL_REPORT_CHARS = 4500

_THEME_KEYWORDS = {
    "ai": ("软件", "互联网", "IT服务", "计算机", "数字媒体", "游戏", "文化传媒"),
    "semi": ("半导体", "元件", "电子化学品", "光学光电子", "其他电子"),
    "robot": ("自动化设备", "通用设备", "专用设备", "电机", "机器人"),
    "auto": ("汽车", "乘用车", "商用车", "电池", "摩托车"),
    "energy": ("光伏", "风电", "电网", "电力", "能源", "煤炭", "油气", "燃气", "电池"),
    "bio": ("医疗", "医药", "制药", "生物", "中药", "医院", "血液", "医疗器械"),
    "space": ("航天", "航空", "军工", "船舶"),
    "security": ("网络安全", "软件", "计算机设备", "通信服务"),
    "tech": ("通信", "消费电子", "计算机", "软件", "光学光电子", "元件", "传媒", "影视", "院线", "电商"),
    "consumer": ("消费电子", "家电", "食品", "饮料", "白酒", "零售", "连锁", "纺织", "美容"),
    "macro": ("证券", "银行", "保险", "多元金融", "房地产"),
    "science": ("环保", "新材料", "化学", "教育", "科研"),
}

_RATING_SCORE = {
    "强烈推荐": 10.0,
    "买入": 9.0,
    "推荐": 8.0,
    "增持": 7.0,
    "优于大市": 6.0,
    "审慎增持": 5.0,
    "中性": 1.0,
    "持有": 1.0,
    "减持": -8.0,
    "卖出": -10.0,
}


class RecommendationError(RuntimeError):
    """每日荐股生成失败。"""


def _now() -> datetime:
    return datetime.now(BEIJING)


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _clip(value: float, lower: float, upper: float) -> float:
    return min(max(value, lower), upper)


def _date_text(value: Any) -> str:
    return str(value or "")[:10]


def _report_pdf_url(row: dict) -> str | None:
    info_code = str(row.get("infoCode") or "").strip()
    return astock.pdf_url(info_code) if info_code else None


def _report_metadata(row: dict) -> dict:
    return {
        "info_code": str(row.get("infoCode") or ""),
        "title": str(row.get("title") or ""),
        "publish_date": _date_text(row.get("publishDate")),
        "organization": str(row.get("orgSName") or row.get("orgName") or ""),
        "researcher": str(row.get("researcher") or ""),
        "rating": str(row.get("emRatingName") or row.get("sRatingName") or ""),
        "industry": str(row.get("indvInduName") or row.get("industryName") or ""),
        "predicted_eps": {
            "this_year": _safe_float(row.get("predictThisYearEps")),
            "next_year": _safe_float(row.get("predictNextYearEps")),
            "next_two_year": _safe_float(row.get("predictNextTwoYearEps")),
        },
        "predicted_pe": {
            "this_year": _safe_float(row.get("predictThisYearPe")),
            "next_year": _safe_float(row.get("predictNextYearPe")),
            "next_two_year": _safe_float(row.get("predictNextTwoYearPe")),
        },
        "pages": int(_safe_float(row.get("attachPages")) or 0),
        "pdf_url": _report_pdf_url(row),
    }


def _latest_report(code: str) -> dict | None:
    try:
        rows = astock.eastmoney_reports(code, max_pages=1)
    except Exception:
        return None
    if not rows:
        return None
    rows.sort(key=lambda item: str(item.get("publishDate") or ""), reverse=True)
    metadata = _report_metadata(rows[0])
    metadata["_raw"] = rows[0]
    return metadata


def _download_pdf(url: str) -> bytes:
    session = requests.Session()
    session.headers.update({"User-Agent": astock.UA, "Referer": "https://data.eastmoney.com/"})
    response = session.get(url, timeout=35, stream=True)
    response.raise_for_status()
    content_length = int(response.headers.get("content-length") or 0)
    if content_length > _PDF_MAX_BYTES:
        raise RecommendationError("研报 PDF 超过读取上限")
    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_content(64 * 1024):
        if not chunk:
            continue
        size += len(chunk)
        if size > _PDF_MAX_BYTES:
            raise RecommendationError("研报 PDF 超过读取上限")
        chunks.append(chunk)
    content = b"".join(chunks)
    if not content.startswith(b"%PDF"):
        raise RecommendationError("研报链接未返回 PDF")
    return content


def _extract_pdf_text(content: bytes, max_chars: int) -> tuple[str, int]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RecommendationError("缺少 pypdf，无法读取研报正文：pip install pypdf") from exc
    reader = PdfReader(io.BytesIO(content))
    text_parts: list[str] = []
    pages_read = 0
    for page in reader.pages[:_PDF_MAX_PAGES]:
        pages_read += 1
        text = page.extract_text() or ""
        text_parts.append(text)
        if sum(len(part) for part in text_parts) >= max_chars:
            break
    text = re.sub(r"\s+", " ", "\n".join(text_parts)).strip()
    return text[:max_chars], pages_read


def _read_report(metadata: dict, max_chars: int) -> dict:
    report = {key: value for key, value in metadata.items() if key != "_raw"}
    report.update({"read_status": "unavailable", "pages_read": 0, "excerpt": "", "read_error": None})
    url = report.get("pdf_url")
    if not url:
        report["read_error"] = "研报无 PDF 链接"
        return report
    try:
        content = _download_pdf(url)
        text, pages_read = _extract_pdf_text(content, max_chars)
        report["pages_read"] = pages_read
        report["excerpt"] = text
        report["read_status"] = "ok" if text else "empty"
        if not text:
            report["read_error"] = "PDF 无可提取文字，可能是扫描件"
    except Exception as exc:
        report["read_error"] = str(exc)[:300]
    return report


def _theme_for_industry(industry_name: str) -> str | None:
    for key, keywords in _THEME_KEYWORDS.items():
        if any(keyword in industry_name for keyword in keywords):
            return key
    return None


def _radar_map(radar: dict) -> dict[str, dict]:
    return {
        str(industry.get("key") or ""): industry
        for industry in radar.get("industries") or []
        if isinstance(industry, dict)
    }


def _direction_news(industry_name: str, radar_by_key: dict[str, dict]) -> tuple[list[dict], float]:
    theme = _theme_for_industry(industry_name)
    evidence_scope = "direction"
    if not theme or theme not in radar_by_key:
        theme = "macro"
        evidence_scope = "market"
    items = (radar_by_key.get(theme) or {}).get("items") or []
    selected = [{
        "title": str(item.get("title") or ""),
        "source": str(item.get("source") or ""),
        "time": str(item.get("time") or ""),
        "url": str(item.get("url") or ""),
        "scope": evidence_scope,
    } for item in items[:5]]
    score = min(len(items), 20) / 2.0
    return selected, score if evidence_scope == "direction" else score * 0.35


def _sector_flow_map(overview: dict) -> dict[str, dict]:
    return {
        str(row.get("name") or ""): row
        for row in overview.get("sectors") or []
        if isinstance(row, dict)
    }


def _direction_score(row: dict, rank: int, news_score: float, flow: dict | None) -> tuple[float, dict]:
    change_pct = _safe_float(row.get("change_pct")) or 0.0
    up_count = int(_safe_float(row.get("up_count")) or 0)
    down_count = int(_safe_float(row.get("down_count")) or 0)
    breadth = up_count / max(up_count + down_count, 1)
    flow_net = _safe_float((flow or {}).get("net")) or 0.0
    rank_component = max(0.0, 30.0 - (rank - 1) * 1.1)
    change_component = _clip(change_pct * 3.0 + 10.0, 0.0, 30.0)
    breadth_component = breadth * 20.0
    flow_component = _clip(flow_net / 2.0, -10.0, 10.0)
    total = rank_component + change_component + breadth_component + flow_component + news_score
    return round(total, 2), {
        "rank": round(rank_component, 2),
        "change": round(change_component, 2),
        "breadth": round(breadth_component, 2),
        "fund_flow": round(flow_component, 2),
        "news": round(news_score, 2),
    }


def _report_score(report: dict | None) -> tuple[float, dict]:
    if not report:
        return -10.0, {"availability": -10.0, "rating": 0.0, "recency": 0.0, "forecast": 0.0}
    rating = _RATING_SCORE.get(str(report.get("rating") or ""), 2.0)
    recency = 0.0
    try:
        published = datetime.strptime(str(report.get("publish_date")), "%Y-%m-%d").date()
        age = max((_now().date() - published).days, 0)
        recency = _clip(8.0 - age / 30.0, 0.0, 8.0)
    except ValueError:
        pass
    pe = _safe_float((report.get("predicted_pe") or {}).get("this_year"))
    forecast = 0.0 if pe is None or pe <= 0 else _clip(8.0 - pe / 10.0, -2.0, 7.0)
    total = 5.0 + rating + recency + forecast
    return round(total, 2), {
        "availability": 5.0,
        "rating": round(rating, 2),
        "recency": round(recency, 2),
        "forecast": round(forecast, 2),
    }


def _candidate_score(stock: dict, amount_max: float, report: dict | None) -> tuple[float, dict]:
    pct = _safe_float(stock.get("pct")) or 0.0
    amount = max(_safe_float(stock.get("amount")) or 0.0, 0.0)
    liquidity = 18.0 * math.log1p(amount) / max(math.log1p(amount_max), 1.0)
    momentum = _clip(pct * 2.0 + 10.0, -8.0, 25.0)
    research, report_parts = _report_score(report)
    total = liquidity + momentum + research
    return round(total, 2), {
        "liquidity": round(liquidity, 2),
        "momentum": round(momentum, 2),
        "research": round(research, 2),
        "research_detail": report_parts,
    }


def _candidate_metadata(stocks: list[dict]) -> list[dict]:
    def enrich(stock: dict) -> dict:
        report = _latest_report(str(stock.get("code") or ""))
        amount_max = max((_safe_float(item.get("amount")) or 0.0 for item in stocks), default=1.0)
        score, breakdown = _candidate_score(stock, amount_max, report)
        return {
            "code": str(stock.get("code") or ""),
            "name": str(stock.get("name") or ""),
            "price": _safe_float(stock.get("price")),
            "change_pct": _safe_float(stock.get("pct")),
            "amount": _safe_float(stock.get("amount")),
            "market_cap": _safe_float(stock.get("mcap")),
            "float_market_cap": _safe_float(stock.get("float_cap")),
            "industry": str(stock.get("industry") or ""),
            "score": score,
            "score_breakdown": breakdown,
            "report": report,
        }

    with ThreadPoolExecutor(max_workers=6) as executor:
        return list(executor.map(enrich, stocks))


def _read_candidate_reports(candidates: list[dict]) -> None:
    def read(candidate: dict) -> tuple[str, dict | None]:
        metadata = candidate.get("report")
        return candidate["code"], _read_report(metadata, _CANDIDATE_REPORT_CHARS) if metadata else None

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(read, candidate) for candidate in candidates]
        reports = {code: report for code, report in (future.result() for future in as_completed(futures))}
    for candidate in candidates:
        candidate["report"] = reports.get(candidate["code"])


def _quote_candidates(candidates: list[dict]) -> None:
    try:
        quotes = astock.tencent_quote([candidate["code"] for candidate in candidates])
    except Exception:
        quotes = {}
    for candidate in candidates:
        quote = quotes.get(candidate["code"]) or {}
        candidate["pe_ttm"] = _safe_float(quote.get("pe_ttm"))
        candidate["pb"] = _safe_float(quote.get("pb"))
        candidate["turnover_pct"] = _safe_float(quote.get("turnover_pct"))
        candidate["volume_ratio"] = _safe_float(quote.get("vol_ratio"))


def _build_directions(radar: dict, overview: dict) -> list[dict]:
    industry_data = astock.industry_comparison(top_n=35)
    industry_rows = industry_data.get("top") or []
    if not industry_rows:
        raise RecommendationError("未获取到行业涨跌与成分数据")
    radar_by_key = _radar_map(radar)
    flows = _sector_flow_map(overview)
    directions: list[dict] = []
    selected_codes: set[str] = set()
    scored_industries = []
    for industry_rank, row in enumerate(industry_rows, 1):
        name = str(row.get("name") or "")
        news, news_score = _direction_news(name, radar_by_key)
        flow = flows.get(name)
        score, breakdown = _direction_score(row, industry_rank, news_score, flow)
        scored_industries.append((score, industry_rank, row, news, flow, breakdown))
    scored_industries.sort(key=lambda item: item[0], reverse=True)
    for direction_score, industry_rank, row, news, flow, direction_breakdown in scored_industries:
        if len(directions) >= 5:
            break
        name = str(row.get("name") or "")
        board_code = str(row.get("code") or "")
        constituents = [
            stock for stock in astock.industry_constituents(board_code, n=18)
            if stock.get("code") and stock.get("name")
            and "ST" not in str(stock.get("name")).upper()
            and "退" not in str(stock.get("name"))
            and (_safe_float(stock.get("price")) or 0) > 0
        ]
        if len(constituents) < 5:
            continue
        metadata_candidates = _candidate_metadata(constituents[:12])
        metadata_candidates.sort(
            key=lambda item: (item.get("report") is not None, item["score"]),
            reverse=True,
        )
        report_candidates = [item for item in metadata_candidates if item.get("report")][:8]
        if len(report_candidates) < 5:
            continue
        _read_candidate_reports(report_candidates)
        candidates = [
            item for item in report_candidates
            if (item.get("report") or {}).get("read_status") == "ok"
        ][:5]
        if len(candidates) != 5:
            continue
        candidate_codes = {candidate["code"] for candidate in candidates}
        if len(candidate_codes & selected_codes) >= 2:
            continue
        _quote_candidates(candidates)
        candidates.sort(key=lambda item: item["score"], reverse=True)
        for candidate_rank, candidate in enumerate(candidates, 1):
            candidate["rank"] = candidate_rank
        up_count = int(_safe_float(row.get("up_count")) or 0)
        down_count = int(_safe_float(row.get("down_count")) or 0)
        directions.append({
            "rank": len(directions) + 1,
            "name": name,
            "board_code": board_code,
            "industry_rank": industry_rank,
            "change_pct": _safe_float(row.get("change_pct")),
            "up_count": up_count,
            "down_count": down_count,
            "breadth": round(up_count / max(up_count + down_count, 1), 4),
            "fund_flow": flow,
            "news": news,
            "score": direction_score,
            "score_breakdown": direction_breakdown,
            "stocks": candidates,
        })
        selected_codes.update(candidate_codes)
    if len(directions) != 5 or any(len(direction["stocks"]) != 5 for direction in directions):
        raise RecommendationError("无法构建完整的五方向×五股票候选池")
    directions.sort(key=lambda item: item["score"], reverse=True)
    for direction_rank, direction in enumerate(directions, 1):
        direction["rank"] = direction_rank
    return directions


def _market_snapshot(overview: dict) -> dict:
    try:
        indices = astock.index_quote()
    except Exception:
        indices = []
    sentiment = overview.get("sentiment") or {}
    sector_flows = overview.get("sectors") or []
    return {
        "indices": indices,
        "sentiment": sentiment,
        "leading_fund_flows": sector_flows[:5],
        "lagging_fund_flows": sector_flows[-5:] if sector_flows else [],
        "updated": overview.get("updated"),
    }


def _json_from_text(text: str) -> dict | None:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip(), flags=re.I | re.S)
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", cleaned):
        try:
            value, _ = decoder.raw_decode(cleaned[match.start():])
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            continue
    return None


def _call_ai(system_prompt: str, user_prompt: str) -> tuple[dict | None, str | None, str]:
    config = aiconfig.load()
    if not config:
        return None, "未配置 AI，使用可解释量化降级结果", "quantitative_fallback"
    try:
        provider = str(config.get("provider") or "")
        if provider.startswith("cli-"):
            kind = provider[4:]
            if not cli_runtime.detect_cli(kind):
                raise RecommendationError(f"未检测到 {kind} CLI")
            raw = cli_runtime.run_cli(kind, system_prompt, user_prompt)
        else:
            response = chat._call_llm(
                config,
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                use_tools=False,
            )
            raw = response["choices"][0]["message"].get("content") or ""
        parsed = _json_from_text(raw)
        if not parsed:
            raise RecommendationError("AI 未返回有效 JSON")
        return parsed, None, "ai"
    except Exception as exc:
        return None, f"AI 综合研判失败，使用可解释量化降级结果：{str(exc)[:180]}", "quantitative_fallback"


def _selection_context(market_snapshot: dict, directions: list[dict]) -> dict:
    return {
        "market": market_snapshot,
        "directions": [{
            "rank": direction["rank"],
            "name": direction["name"],
            "change_pct": direction["change_pct"],
            "breadth": direction["breadth"],
            "fund_flow": direction["fund_flow"],
            "news": direction["news"],
            "score": direction["score"],
            "stocks": [{
                "code": stock["code"],
                "name": stock["name"],
                "price": stock["price"],
                "change_pct": stock["change_pct"],
                "amount": stock["amount"],
                "pe_ttm": stock["pe_ttm"],
                "pb": stock["pb"],
                "turnover_pct": stock["turnover_pct"],
                "volume_ratio": stock["volume_ratio"],
                "score": stock["score"],
                "report": stock["report"],
            } for stock in direction["stocks"]],
        } for direction in directions],
    }


def _fallback_selection(market_snapshot: dict, directions: list[dict]) -> dict:
    choices = [
        (direction["score"] * 0.35 + stock["score"], direction, stock)
        for direction in directions
        for stock in direction["stocks"]
    ]
    _, direction, stock = max(choices, key=lambda item: item[0])
    report = stock.get("report") or {}
    sentiment = market_snapshot.get("sentiment") or {}
    breadth = sentiment.get("breadth") or "数据不足"
    speculation = sentiment.get("speculation") or "数据不足"
    return {
        "selected_code": stock["code"],
        "selected_name": stock["name"],
        "selected_direction": direction["name"],
        "confidence": "低",
        "thesis": (
            f"{stock['name']}在{direction['name']}候选中兼具相对强度、成交活跃度与研报覆盖，"
            "因此成为量化降级模式下的次日观察标的。"
        ),
        "market_analysis": (
            f"市场宽度为{breadth}，题材活跃度为{speculation}；"
            f"{direction['name']}当日涨跌幅为{direction['change_pct']}%，"
            f"上涨家数{direction['up_count']}、下跌家数{direction['down_count']}。"
        ),
        "direction_analysis": (
            f"{direction['name']}方向综合分{direction['score']}，"
            f"资讯证据{len(direction['news'])}条，板块内候选按行情、流动性与研报覆盖排序。"
        ),
        "candidate_comparison": (
            f"{stock['name']}候选分{stock['score']}，当日涨跌幅{stock['change_pct']}%，"
            f"最新研报为《{report.get('title') or '暂无'}》。"
        ),
        "catalysts": [item["title"] for item in direction["news"][:3]]
        + ([report.get("title")] if report.get("title") else []),
        "risks": [
            "量化降级结果未经过大模型语义综合，置信度较低",
            "单日板块强势可能发生快速轮动",
            "研报预测依赖其假设，不能视为确定事实",
        ],
        "invalidation_conditions": [
            "板块由强转弱且资金流显著恶化",
            "公司出现未被当前数据覆盖的重大利空",
            "成交活跃度和相对强度同时快速下降",
        ],
    }


def _select_stock(market_snapshot: dict, directions: list[dict]) -> tuple[dict, str, list[str]]:
    context = _selection_context(market_snapshot, directions)
    system_prompt = (
        "你是审慎的A股短线研究员。只能使用给定证据，在固定25只候选中选择唯一一只"
        "作为下一交易日观察标的。禁止编造数据、目标价或收益承诺。必须同时陈述市场环境、"
        "方向逻辑、候选比较、催化、风险与失效条件。只输出JSON。"
    )
    user_prompt = (
        "从以下五个方向、每方向五只候选中综合评判。研报 excerpt 是实际PDF正文提取内容；"
        "read_status 非 ok 时不得假装读过正文。输出字段：selected_code、selected_name、"
        "selected_direction、confidence（低/中/较高）、thesis、market_analysis、"
        "direction_analysis、candidate_comparison、catalysts（数组）、risks（数组）、"
        "invalidation_conditions（数组）。selected_code必须来自候选。\n\n"
        + json.dumps(context, ensure_ascii=False)
    )
    parsed, warning, mode = _call_ai(system_prompt, user_prompt)
    valid_codes = {
        stock["code"]: (direction, stock)
        for direction in directions
        for stock in direction["stocks"]
    }
    warnings = [warning] if warning else []
    selected_code = str((parsed or {}).get("selected_code") or "")
    required_text = (
        "thesis", "market_analysis", "direction_analysis", "candidate_comparison",
    )
    required_lists = ("catalysts", "risks", "invalidation_conditions")
    ai_complete = bool(parsed) and all(
        str(parsed.get(key) or "").strip() for key in required_text
    ) and all(
        isinstance(parsed.get(key), list) and bool(parsed.get(key)) for key in required_lists
    )
    if selected_code not in valid_codes or not ai_complete:
        if parsed:
            reason = "选择不在候选池内" if selected_code not in valid_codes else "返回字段不完整"
            warnings.append(f"AI {reason}，已回退到可解释量化排序")
        return _fallback_selection(market_snapshot, directions), "quantitative_fallback", warnings
    direction, stock = valid_codes[selected_code]
    result = {
        "selected_code": selected_code,
        "selected_name": stock["name"],
        "selected_direction": direction["name"],
        "confidence": str(parsed.get("confidence") or "低"),
        "thesis": str(parsed.get("thesis") or ""),
        "market_analysis": str(parsed.get("market_analysis") or ""),
        "direction_analysis": str(parsed.get("direction_analysis") or ""),
        "candidate_comparison": str(parsed.get("candidate_comparison") or ""),
        "catalysts": [str(item) for item in parsed.get("catalysts") or []][:6],
        "risks": [str(item) for item in parsed.get("risks") or []][:8],
        "invalidation_conditions": [
            str(item) for item in parsed.get("invalidation_conditions") or []
        ][:6],
    }
    return result, mode, warnings


def _final_report_rows(code: str) -> list[dict]:
    try:
        rows = astock.eastmoney_reports(code, max_pages=1)
    except Exception:
        return []
    rows.sort(key=lambda item: str(item.get("publishDate") or ""), reverse=True)
    distinct: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        info_code = str(row.get("infoCode") or "")
        if not info_code or info_code in seen:
            continue
        seen.add(info_code)
        distinct.append(_report_metadata(row))
        if len(distinct) >= 3:
            break
    return distinct


def _read_final_reports(code: str) -> list[dict]:
    rows = _final_report_rows(code)
    with ThreadPoolExecutor(max_workers=3) as executor:
        return list(executor.map(lambda report: _read_report(report, _FINAL_REPORT_CHARS), rows))


def _fallback_report_analysis(reports: list[dict]) -> dict:
    per_report = []
    for report in reports:
        excerpt = str(report.get("excerpt") or "")
        per_report.append({
            "info_code": report.get("info_code"),
            "summary": (
                f"{report.get('organization') or '研究机构'}在《{report.get('title') or '未命名研报'}》"
                f"中给出{report.get('rating') or '未披露'}评级。"
            ),
            "positive_factors": [excerpt[:260]] if excerpt else [],
            "risk_factors": [report.get("read_error")] if report.get("read_error") else [],
            "forecast_assumptions": [
                f"预测PE：{report.get('predicted_pe')}",
                f"预测EPS：{report.get('predicted_eps')}",
            ],
        })
    return {
        "overall": "以下为研报元数据与正文摘录的机械整理，未进行AI语义归纳。",
        "consensus": "多份研报的评级、盈利预测与正文摘录见逐篇分析。",
        "differences": "降级模式不推断机构间差异。",
        "key_assumptions": ["研报预测成立依赖其正文假设与行业环境"],
        "report_risks": ["机构研报可能存在滞后、偏差或利益冲突"],
        "per_report": per_report,
    }


def _analyze_final_reports(selection: dict, reports: list[dict]) -> tuple[dict, str, list[str]]:
    system_prompt = (
        "你是严谨的卖方研报审阅员。只能根据给定研报元数据和PDF正文摘录做交叉分析，"
        "区分机构观点与客观事实，不得补写正文没有的信息。只输出JSON。"
    )
    user_prompt = (
        f"分析{selection['selected_name']}（{selection['selected_code']}）的研报。"
        "输出字段：overall、consensus、differences、key_assumptions（数组）、"
        "report_risks（数组）、per_report（数组，每项含info_code、summary、"
        "positive_factors数组、risk_factors数组、forecast_assumptions数组）。"
        "read_status非ok时必须说明正文未成功读取。\n\n"
        + json.dumps(reports, ensure_ascii=False)
    )
    parsed, warning, mode = _call_ai(system_prompt, user_prompt)
    warnings = [warning] if warning else []
    report_rows = parsed.get("per_report") if parsed else None
    parsed_codes = {
        str(item.get("info_code") or "")
        for item in report_rows or []
        if isinstance(item, dict)
    }
    required_codes = {str(report.get("info_code") or "") for report in reports}
    ai_complete = bool(parsed) and all(
        str(parsed.get(key) or "").strip() for key in ("overall", "consensus", "differences")
    ) and isinstance(report_rows, list) and required_codes <= parsed_codes
    if not ai_complete:
        if parsed:
            warnings.append("AI 研报分析字段不完整，已回退到研报元数据与正文机械整理")
        return _fallback_report_analysis(reports), "quantitative_fallback", warnings
    by_info_code = {
        str(item.get("info_code") or ""): item
        for item in parsed.get("per_report") or []
        if isinstance(item, dict)
    }
    normalized_reports = []
    for report in reports:
        info_code = str(report.get("info_code") or "")
        item = by_info_code.get(info_code) or {}
        normalized_reports.append({
            "info_code": info_code,
            "summary": str(item.get("summary") or ""),
            "positive_factors": [str(value) for value in item.get("positive_factors") or []][:6],
            "risk_factors": [str(value) for value in item.get("risk_factors") or []][:6],
            "forecast_assumptions": [
                str(value) for value in item.get("forecast_assumptions") or []
            ][:6],
        })
    return {
        "overall": str(parsed.get("overall") or ""),
        "consensus": str(parsed.get("consensus") or ""),
        "differences": str(parsed.get("differences") or ""),
        "key_assumptions": [str(value) for value in parsed.get("key_assumptions") or []][:8],
        "report_risks": [str(value) for value in parsed.get("report_risks") or []][:8],
        "per_report": normalized_reports,
    }, mode, warnings


def _attach_report_analysis(reports: list[dict], analysis: dict) -> list[dict]:
    analyses = {
        str(item.get("info_code") or ""): item
        for item in analysis.get("per_report") or []
        if isinstance(item, dict)
    }
    return [{
        **report,
        "analysis": analyses.get(str(report.get("info_code") or ""), {}),
    } for report in reports]


def _validate_report(report: dict) -> None:
    directions = report.get("directions") or []
    if len(directions) != 5:
        raise RecommendationError("报告方向数量不是5")
    candidates = [
        stock
        for direction in directions
        for stock in direction.get("stocks") or []
    ]
    if any(len(direction.get("stocks") or []) != 5 for direction in directions):
        raise RecommendationError("报告候选股票不是每方向5只")
    codes = {str(stock.get("code") or "") for stock in candidates}
    selected_code = str((report.get("recommendation") or {}).get("code") or "")
    if selected_code not in codes:
        raise RecommendationError("最终股票不在25只候选池内")
    if not (report.get("recommendation") or {}).get("reports"):
        raise RecommendationError("最终股票缺少对应研报")


def generate_daily_report(force: bool = False) -> dict:
    """生成并持久化今天的荐股研究报告。"""
    if not _GENERATE_LOCK.acquire(blocking=False):
        raise RecommendationError("每日荐股正在生成，请稍后再试")
    try:
        now = _now()
        report_file = REPORT_DIR / f"{now.date().isoformat()}.json"
        if not force:
            existing = _read_json(report_file)
            if existing:
                return existing
        try:
            radar = newsradar.fetch_radar()
        except Exception:
            radar = newsradar.get_radar(force=False)
        try:
            overview = market.get_overview()
        except Exception:
            overview = {}
        directions = _build_directions(radar, overview)
        market_snapshot = _market_snapshot(overview)
        selection, selection_mode, selection_warnings = _select_stock(market_snapshot, directions)
        final_reports = _read_final_reports(selection["selected_code"])
        if not final_reports:
            candidate_report = next(
                (
                    stock.get("report")
                    for direction in directions
                    for stock in direction["stocks"]
                    if stock["code"] == selection["selected_code"] and stock.get("report")
                ),
                None,
            )
            final_reports = [candidate_report] if candidate_report else []
        report_analysis, report_mode, report_warnings = _analyze_final_reports(selection, final_reports)
        recommendation = {
            "code": selection["selected_code"],
            "name": selection["selected_name"],
            "direction": selection["selected_direction"],
            "confidence": selection["confidence"],
            "horizon": "下一交易日（短线观察）",
            "thesis": selection["thesis"],
            "market_analysis": selection["market_analysis"],
            "direction_analysis": selection["direction_analysis"],
            "candidate_comparison": selection["candidate_comparison"],
            "catalysts": selection["catalysts"],
            "risks": selection["risks"],
            "invalidation_conditions": selection["invalidation_conditions"],
            "report_analysis": {
                key: value for key, value in report_analysis.items() if key != "per_report"
            },
            "reports": _attach_report_analysis(final_reports, report_analysis),
        }
        modes = {selection_mode, report_mode}
        result = {
            "version": 1,
            "date": now.date().isoformat(),
            "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "analysis_mode": "ai" if modes == {"ai"} else "quantitative_fallback",
            "warnings": list(dict.fromkeys(
                warning for warning in selection_warnings + report_warnings if warning
            )),
            "market": market_snapshot,
            "source_summary": {
                "radar_generated_at": radar.get("generated_at"),
                "radar_items": sum(
                    len(industry.get("items") or [])
                    for industry in radar.get("industries") or []
                ),
                "candidate_reports_read": sum(
                    1
                    for direction in directions
                    for stock in direction["stocks"]
                    if (stock.get("report") or {}).get("read_status") == "ok"
                ),
                "candidate_reports_total": 25,
                "final_reports_read": sum(
                    1 for report in final_reports if report.get("read_status") == "ok"
                ),
                "final_reports_total": len(final_reports),
            },
            "directions": directions,
            "recommendation": recommendation,
            "disclaimer": (
                "本报告是基于公开数据和用户自有AI生成的高风险研究参考，不构成投资建议。"
                "所谓“最可能上涨”仅表示候选池内的相对排序，不代表上涨概率或收益承诺。"
                "数据、研报与模型均可能出错或滞后，请独立核实并自行承担风险。"
            ),
        }
        _validate_report(result)
        _atomic_json(report_file, result)
        _atomic_json(LATEST_FILE, result)
        return result
    finally:
        _GENERATE_LOCK.release()


def get_latest() -> dict | None:
    """读取最近一次生成结果。"""
    return _read_json(LATEST_FILE)


def get_report(report_date: str) -> dict | None:
    """按 YYYY-MM-DD 读取历史报告。"""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(report_date or "")):
        return None
    return _read_json(REPORT_DIR / f"{report_date}.json")


def list_history(limit: int = 30) -> list[dict]:
    """列出历史报告摘要。"""
    if not REPORT_DIR.exists():
        return []
    rows = []
    for path in sorted(REPORT_DIR.glob("????-??-??.json"), reverse=True)[:max(1, min(limit, 100))]:
        report = _read_json(path)
        if not report:
            continue
        recommendation = report.get("recommendation") or {}
        rows.append({
            "date": report.get("date"),
            "generated_at": report.get("generated_at"),
            "analysis_mode": report.get("analysis_mode"),
            "code": recommendation.get("code"),
            "name": recommendation.get("name"),
            "direction": recommendation.get("direction"),
            "confidence": recommendation.get("confidence"),
        })
    return rows


def get_dashboard(limit: int = 30) -> dict:
    """前端一次读取最新报告和历史摘要。"""
    return {"latest": get_latest(), "history": list_history(limit)}
