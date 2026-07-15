"""通用定时任务调度 —— daemon 线程 + 配置持久化。

存储 ~/.vibe-research/tasks.json（仓库外，同 portfolio 范式，VR_DATA_DIR 可覆盖）。
串行调度：到点执行注册的回调，前一次没跑完则跳过本次（避免雷达抓取叠加）。
任务状态只存最近一次（last_run / last_status / last_error），不保留历史。

用法：
    import scheduler
    scheduler.register("radar", "资讯雷达刷新", newsradar.fetch_radar, 1800)
    scheduler.start()
    scheduler.get_tasks()            # 读状态
    scheduler.set_task("radar", True, 3600)  # 开关 / 改间隔
    scheduler.run_once("radar")      # 手动立即执行
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from datetime import datetime, timezone, timedelta

BEIJING = timezone(timedelta(hours=8))
CACHE_DIR = os.environ.get("VR_DATA_DIR") or os.path.join(os.path.expanduser("~"), ".vibe-research")
TASKS_FILE = os.path.join(CACHE_DIR, "tasks.json")

_LOCK = threading.Lock()          # 保护 _CONFIG / _REGISTRY 的读写
_RUN_LOCK = threading.Lock()      # 串行执行：同一时刻只跑一个任务

# key -> {name, fn, default_interval}
_REGISTRY: dict[str, dict] = {}

# 内存中的运行配置 + 状态；落盘到 TASKS_FILE
# key -> {enabled, interval_sec, last_run, last_status, last_error}
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


def register(key: str, name: str, fn, default_interval: int) -> None:
    """注册一个任务。在 app 启动时、start() 之前调用。磁盘上已有的配置会保留。"""
    with _LOCK:
        _REGISTRY[key] = {"name": name, "fn": fn, "default_interval": default_interval}
        # 优先读磁盘已有配置；磁盘没有该 key 才用默认值初始化落盘。
        # 注意：不能先用默认值 _save —— 那会在 _init_from_disk 读盘前把磁盘上的用户配置冲掉。
        disk = _load()
        if key in disk and isinstance(disk[key], dict):
            e = disk[key]
            _CONFIG[key] = {
                "enabled": bool(e.get("enabled", False)),
                "interval_sec": int(e.get("interval_sec", default_interval) or default_interval),
                "last_run": e.get("last_run"),
                "last_status": e.get("last_status"),
                "last_error": e.get("last_error"),
            }
        elif key not in _CONFIG:
            _CONFIG[key] = {
                "enabled": False,
                "interval_sec": default_interval,
                "last_run": None,
                "last_status": None,
                "last_error": None,
            }
            _save(_CONFIG)


def _init_from_disk() -> None:
    """启动时同步磁盘配置到内存（register 已读过一次，这里兜底处理 register 之后被外部改写的磁盘）。"""
    with _LOCK:
        disk = _load()
        for key, reg in _REGISTRY.items():
            if key in disk and isinstance(disk[key], dict):
                e = disk[key]
                cur = _CONFIG.get(key, {})
                # 只补字段、不覆盖运行中状态（last_status=running 是内存态，磁盘上可能是旧值）
                _CONFIG[key] = {
                    "enabled": bool(e.get("enabled", cur.get("enabled", False))),
                    "interval_sec": int(e.get("interval_sec", cur.get("interval_sec", reg["default_interval"])) or reg["default_interval"]),
                    "last_run": e.get("last_run", cur.get("last_run")),
                    "last_status": cur.get("last_status", e.get("last_status")),
                    "last_error": e.get("last_error", cur.get("last_error")),
                }


def _next_run_est(cfg: dict) -> str | None:
    """预估下次执行时间（仅展示用，非精确承诺）。"""
    if not cfg.get("enabled") or not cfg.get("last_run"):
        return None
    try:
        last = datetime.strptime(cfg["last_run"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=BEIJING)
        nxt = last + timedelta(seconds=cfg.get("interval_sec", 0))
        return nxt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
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
                "interval_sec": cfg.get("interval_sec", reg["default_interval"]),
                "last_run": cfg.get("last_run"),
                "last_status": cfg.get("last_status"),
                "last_error": cfg.get("last_error"),
                "next_run_est": _next_run_est(cfg),
            })
        return out


def set_task(key: str, enabled: bool | None = None, interval_sec: int | None = None) -> dict | None:
    """开关 / 改间隔。返回更新后的任务状态，key 不存在返回 None。"""
    with _LOCK:
        if key not in _REGISTRY:
            return None
        cfg = _CONFIG.setdefault(key, {})
        if enabled is not None:
            cfg["enabled"] = bool(enabled)
        if interval_sec is not None and interval_sec >= 60:  # 最低 1 分钟，防误设过短把源打爆
            cfg["interval_sec"] = int(interval_sec)
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
        "interval_sec": cfg.get("interval_sec", reg["default_interval"]),
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
                last_run = cfg.get("last_run")
                if not last_run:
                    # 开了但从未跑过：立即触发一次
                    due.append(key)
                    continue
                try:
                    last_ts = datetime.strptime(last_run, "%Y-%m-%d %H:%M:%S").replace(tzinfo=BEIJING).timestamp()
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
