"""通用定时任务调度 —— daemon 线程 + 配置持久化。

存储 ~/.vibe-research/tasks.json（仓库外，同 portfolio 范式，VR_DATA_DIR 可覆盖）。
串行调度：到点执行注册的回调，前一次没跑完则跳过本次（避免雷达抓取叠加）。
任务状态只存最近一次（last_run / last_status / last_error），不保留历史。
支持 interval（固定间隔）和 daily（北京时间每日固定 HH:MM）两种任务。

用法：
    import scheduler
    scheduler.register("radar", "资讯雷达刷新", newsradar.fetch_radar, 1800)
    scheduler.register_daily("recommendation", "每日荐股", build_report, "20:00")
    scheduler.start()
    scheduler.get_tasks()            # 读状态
    scheduler.set_task("radar", True, 3600)  # 开关 / 改间隔
    scheduler.set_task("recommendation", daily_time="21:00")
    scheduler.run_once("radar")      # 手动立即执行
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from datetime import datetime, timezone, timedelta
import re

BEIJING = timezone(timedelta(hours=8))
CACHE_DIR = os.environ.get("VR_DATA_DIR") or os.path.join(os.path.expanduser("~"), ".vibe-research")
TASKS_FILE = os.path.join(CACHE_DIR, "tasks.json")

_LOCK = threading.Lock()          # 保护 _CONFIG / _REGISTRY 的读写
_RUN_LOCK = threading.Lock()      # 串行执行：同一时刻只跑一个任务

# key -> {name, fn, schedule_type, default_interval, default_time, default_enabled}
_REGISTRY: dict[str, dict] = {}

# 内存中的运行配置 + 状态；落盘到 TASKS_FILE
# key -> {enabled, schedule_type, interval_sec, daily_time, last_run, last_status, last_error}
_CONFIG: dict[str, dict] = {}

_started = False


def _now() -> str:
    return datetime.now(BEIJING).strftime("%Y-%m-%d %H:%M:%S")


def _load() -> dict:
    try:
        with open(TASKS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(d: dict) -> None:
    # 原子落位（同 portfolio._save）：并发读撞上写中途半截 JSON 会被 _load 当空
    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp = TASKS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, TASKS_FILE)


def _valid_daily_time(value: str | None) -> bool:
    if not re.fullmatch(r"\d{2}:\d{2}", str(value or "")):
        return False
    hour, minute = (int(part) for part in str(value).split(":"))
    return 0 <= hour <= 23 and 0 <= minute <= 59


def _default_config(reg: dict) -> dict:
    return {
        "enabled": reg["default_enabled"],
        "schedule_type": reg["schedule_type"],
        "interval_sec": reg["default_interval"],
        "daily_time": reg["default_time"],
        "last_run": None,
        "last_status": None,
        "last_error": None,
    }


def _normalize_config(entry: dict, reg: dict, current: dict | None = None) -> dict:
    current = current or {}
    schedule_type = reg["schedule_type"]
    daily_time = str(entry.get("daily_time") or current.get("daily_time") or reg["default_time"] or "")
    if schedule_type == "daily" and not _valid_daily_time(daily_time):
        daily_time = reg["default_time"]
    return {
        "enabled": bool(entry.get("enabled", current.get("enabled", reg["default_enabled"]))),
        "schedule_type": schedule_type,
        "interval_sec": int(
            entry.get("interval_sec", current.get("interval_sec", reg["default_interval"]))
            or reg["default_interval"]
        ),
        "daily_time": daily_time or None,
        "last_run": entry.get("last_run", current.get("last_run")),
        "last_status": current.get("last_status", entry.get("last_status")),
        "last_error": entry.get("last_error", current.get("last_error")),
    }


def register(
    key: str,
    name: str,
    fn,
    default_interval: int,
    *,
    default_enabled: bool = False,
) -> None:
    """注册一个任务。在 app 启动时、start() 之前调用。磁盘上已有的配置会保留。"""
    with _LOCK:
        _REGISTRY[key] = {
            "name": name,
            "fn": fn,
            "schedule_type": "interval",
            "default_interval": default_interval,
            "default_time": None,
            "default_enabled": default_enabled,
        }
        # 优先读磁盘已有配置；磁盘没有该 key 才用默认值初始化落盘。
        # 注意：不能先用默认值 _save —— 那会在 _init_from_disk 读盘前把磁盘上的用户配置冲掉。
        disk = _load()
        if key in disk and isinstance(disk[key], dict):
            _CONFIG[key] = _normalize_config(disk[key], _REGISTRY[key])
        elif key not in _CONFIG:
            _CONFIG[key] = _default_config(_REGISTRY[key])
            _save(_CONFIG)


def register_daily(
    key: str,
    name: str,
    fn,
    default_time: str = "20:00",
    *,
    default_enabled: bool = False,
) -> None:
    """注册北京时间每日固定时刻任务。"""
    if not _valid_daily_time(default_time):
        raise ValueError("default_time 必须是有效的 HH:MM")
    with _LOCK:
        _REGISTRY[key] = {
            "name": name,
            "fn": fn,
            "schedule_type": "daily",
            "default_interval": 86400,
            "default_time": default_time,
            "default_enabled": default_enabled,
        }
        disk = _load()
        if key in disk and isinstance(disk[key], dict):
            _CONFIG[key] = _normalize_config(disk[key], _REGISTRY[key])
        elif key not in _CONFIG:
            _CONFIG[key] = _default_config(_REGISTRY[key])
            _save(_CONFIG)


def _init_from_disk() -> None:
    """启动时同步磁盘配置到内存（register 已读过一次，这里兜底处理 register 之后被外部改写的磁盘）。"""
    with _LOCK:
        disk = _load()
        for key, reg in _REGISTRY.items():
            if key in disk and isinstance(disk[key], dict):
                # 只补字段、不覆盖运行中状态（last_status=running 是内存态，磁盘上可能是旧值）
                _CONFIG[key] = _normalize_config(disk[key], reg, _CONFIG.get(key))


def _parse_last_run(value: str | None) -> datetime | None:
    try:
        return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S").replace(tzinfo=BEIJING)
    except (ValueError, TypeError):
        return None


def _next_run_est(cfg: dict, now: datetime | None = None) -> str | None:
    """预估下次执行时间（仅展示用，非精确承诺）。"""
    if not cfg.get("enabled"):
        return None
    if cfg.get("schedule_type") == "daily":
        daily_time = cfg.get("daily_time")
        if not _valid_daily_time(daily_time):
            return None
        now = now or datetime.now(BEIJING)
        hour, minute = (int(part) for part in daily_time.split(":"))
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        last = _parse_last_run(cfg.get("last_run"))
        if last and last.date() == now.date():
            target += timedelta(days=1)
        elif target < now:
            # 今天尚未执行且已过点：属于待执行状态，展示当前计划点。
            pass
        return target.strftime("%Y-%m-%d %H:%M:%S")
    last = _parse_last_run(cfg.get("last_run"))
    if not last:
        return None
    try:
        nxt = last + timedelta(seconds=cfg.get("interval_sec", 0))
        return nxt.strftime("%Y-%m-%d %H:%M:%S")
    except TypeError:
        return None


def get_tasks() -> list[dict]:
    """返回所有任务状态（含 next_run_est 供前端展示）。"""
    with _LOCK:
        out = []
        for key, reg in _REGISTRY.items():
            cfg = _CONFIG.get(key, {})
            out.append({
                "key": key,
                "name": reg["name"],
                "enabled": cfg.get("enabled", False),
                "schedule_type": reg["schedule_type"],
                "interval_sec": cfg.get("interval_sec", reg["default_interval"]),
                "daily_time": cfg.get("daily_time"),
                "last_run": cfg.get("last_run"),
                "last_status": cfg.get("last_status"),
                "last_error": cfg.get("last_error"),
                "next_run_est": _next_run_est(cfg),
            })
        return out


def set_task(
    key: str,
    enabled: bool | None = None,
    interval_sec: int | None = None,
    daily_time: str | None = None,
) -> dict | None:
    """开关 / 改间隔。返回更新后的任务状态，key 不存在返回 None。"""
    with _LOCK:
        if key not in _REGISTRY:
            return None
        cfg = _CONFIG.setdefault(key, {})
        if enabled is not None:
            cfg["enabled"] = bool(enabled)
        if interval_sec is not None and interval_sec >= 60:  # 最低 1 分钟，防误设过短把源打爆
            cfg["interval_sec"] = int(interval_sec)
        if daily_time is not None:
            if _REGISTRY[key]["schedule_type"] != "daily" or not _valid_daily_time(daily_time):
                raise ValueError("daily_time 必须是该每日任务的有效 HH:MM")
            cfg["daily_time"] = daily_time
        _save(_CONFIG)
        return _snapshot(key)


def _snapshot(key: str) -> dict | None:
    reg = _REGISTRY.get(key)
    if not reg:
        return None
    cfg = _CONFIG.get(key, {})
    return {
        "key": key, "name": reg["name"],
        "enabled": cfg.get("enabled", False),
        "schedule_type": reg["schedule_type"],
        "interval_sec": cfg.get("interval_sec", reg["default_interval"]),
        "daily_time": cfg.get("daily_time"),
        "last_run": cfg.get("last_run"), "last_status": cfg.get("last_status"),
        "last_error": cfg.get("last_error"), "next_run_est": _next_run_est(cfg),
    }


def _execute(key: str, manual: bool = False) -> None:
    """执行一个任务（串行）。更新 last_run/last_status/last_error 并落盘。"""
    reg = _REGISTRY.get(key)
    if not reg:
        return
    # 串行：拿不到锁说明有任务在跑，调度场景直接跳过本次；手动执行也跳过（避免叠加抓取）
    if not _RUN_LOCK.acquire(blocking=False):
        if manual:
            raise RuntimeError("有任务正在执行，请稍后再试")
        return
    try:
        with _LOCK:
            _CONFIG.setdefault(key, {})["last_status"] = "running"
            _save(_CONFIG)
        try:
            reg["fn"]()
            with _LOCK:
                cfg = _CONFIG.setdefault(key, {})
                cfg["last_run"] = _now()
                cfg["last_status"] = "ok"
                cfg["last_error"] = None
                _save(_CONFIG)
        except Exception as e:  # noqa: BLE001 — 单任务失败不影响调度线程和其他任务
            with _LOCK:
                cfg = _CONFIG.setdefault(key, {})
                cfg["last_run"] = _now()
                cfg["last_status"] = "error"
                cfg["last_error"] = str(e)[:500]
                _save(_CONFIG)
            if manual:
                raise
            print(f"[scheduler] 任务「{key}」执行失败: {e}", file=sys.stderr)
    finally:
        _RUN_LOCK.release()


def run_once(key: str) -> dict | None:
    """手动立即执行一次（同步等待回调返回）。返回任务状态。"""
    if key not in _REGISTRY:
        return None
    _execute(key, manual=True)
    return _snapshot(key)


def _loop() -> None:
    """daemon 调度线程：每 10s 扫一遍，到点就跑（串行）。"""
    while True:
        time.sleep(10)
        now_ts = time.time()
        with _LOCK:
            due = []
            for key, reg in _REGISTRY.items():
                cfg = _CONFIG.get(key, {})
                if not cfg.get("enabled"):
                    continue
                if reg["schedule_type"] == "daily":
                    daily_time = cfg.get("daily_time")
                    if not _valid_daily_time(daily_time):
                        continue
                    now = datetime.now(BEIJING)
                    hour, minute = (int(part) for part in daily_time.split(":"))
                    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                    last = _parse_last_run(cfg.get("last_run"))
                    if now >= target and (not last or last.date() != now.date()):
                        due.append(key)
                    continue
                last_run = cfg.get("last_run")
                if not last_run:
                    # 开了但从未跑过：立即触发一次
                    due.append(key)
                    continue
                try:
                    parsed = _parse_last_run(last_run)
                    if parsed is None:
                        raise ValueError
                    last_ts = parsed.timestamp()
                except (ValueError, TypeError):
                    due.append(key)
                    continue
                interval = cfg.get("interval_sec", reg["default_interval"])
                if now_ts - last_ts >= interval:
                    due.append(key)
        # 串行执行到点的任务（_execute 内部 _RUN_LOCK 保证不叠加）
        for key in due:
            _execute(key)


def start() -> None:
    """启动调度线程（只启一次）。app 启动时在所有 register 之后调用。"""
    global _started
    if _started:
        return
    _init_from_disk()
    threading.Thread(target=_loop, daemon=True, name="vr-scheduler").start()
    _started = True
