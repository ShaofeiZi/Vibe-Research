"""AI 接入配置持久化 —— 存 ~/.vibe-research/ai-config.json（仓库外，同 portfolio 范式）。

历史：v0.1.x 前端只存 localStorage，后端按请求拿配置用完即弃。
问题：资讯雷达「定时翻译」等后台任务拿不到 AI 配置；多浏览器/多端配置不一致。
现在：后端存一份，前端优先读后端、localStorage 仅作离线缓存与快速首屏。

合规：配置含 API key，明文落盘到用户目录（仓库外、不进 git）。
公网部署须设 VR_API_KEY 鉴权，否则任何人都能读走你的 key。
"""

from __future__ import annotations

import json
import os
import sys
import threading

CACHE_DIR = os.environ.get("VR_DATA_DIR") or os.path.join(os.path.expanduser("~"), ".vibe-research")
CONFIG_FILE = os.path.join(CACHE_DIR, "ai-config.json")
_LOCK = threading.Lock()

# 允许的 provider 白名单（与前端 ai-models.ts ProviderId 对齐），防写入脏值
_VALID_PROVIDERS = {
    "deepseek", "silicon", "openai", "minimax", "openrouter", "groq",
    "together", "mimo", "openai-compatible",
    "cli-claude", "cli-qwen", "cli-deepseek", "cli-codex",
    "cli-opencode", "cli-cursor", "cli-kimi",
}


def _normalize(cfg: dict) -> dict | None:
    """清洗 / 校验。CLI 订阅只认 model；API 接入要 baseURL + apiKey + model。返回 None 表示无效。"""
    if not isinstance(cfg, dict):
        return None
    provider = str(cfg.get("provider", "")).strip()
    if provider and provider not in _VALID_PROVIDERS:
        return None
    model = str(cfg.get("model", "")).strip()
    if not model:
        return None
    is_cli = provider.startswith("cli-")
    base_url = str(cfg.get("baseURL", "")).strip()
    api_key = str(cfg.get("apiKey", "")).strip()
    if not is_cli and (not base_url or not api_key):
        return None
    return {
        "provider": provider,
        "baseURL": base_url,
        "apiKey": api_key,
        "model": model,
    }


def load() -> dict | None:
    """读配置。无配置或损坏返回 None。"""
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return _normalize(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    except Exception as e:  # noqa: BLE001 — 读配置异常不阻塞启动
        print(f"[vibe-research] 读取 AI 配置失败: {e}", file=sys.stderr)
        return None


def save(cfg: dict) -> dict | None:
    """写配置（先校验）。无效返回 None，不落盘。原子写 tmp→replace。"""
    norm = _normalize(cfg)
    if norm is None:
        return None
    with _LOCK:
        os.makedirs(CACHE_DIR, exist_ok=True)
        tmp = CONFIG_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(norm, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_FILE)
    return norm


def clear() -> None:
    """删配置文件。不存在也视为成功。"""
    with _LOCK:
        try:
            os.remove(CONFIG_FILE)
        except FileNotFoundError:
            pass
