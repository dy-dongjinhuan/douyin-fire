# -*- coding: utf-8 -*-
"""AI 文案生成：调用 OpenAI 兼容的 /chat/completions 接口生成随机续火花文案。

每个好友每次运行都会调用一次，保证每次文案不同；调用失败抛异常，
由调用方（app.main）回退到自定义文案池。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

DEFAULT_MAX_TOKENS = 256
DEFAULT_TEMPERATURE = 1.0
DEFAULT_TIMEOUT_S = 15.0

SYSTEM_PROMPT = (
    "你是一个抖音好友互动文案助手。只输出一条短句，口语化、自然、不油腻、"
    "不重复，不含引号、前缀、表情符号堆砌，适合发给关系熟悉的朋友续火花。"
)


def _pick(cfg: dict, key: str, default):
    value = cfg.get(key)
    return default if value is None else value


def generate_ai_text(cfg: dict) -> str:
    """调用 OpenAI 兼容接口生成一条文案；任何失败都抛异常（由调用方回退）。"""
    base_url = str(_pick(cfg, "base_url", "https://api.openai.com/v1")).strip().rstrip("/")
    api_key = str(_pick(cfg, "api_key", "")).strip()
    model = str(_pick(cfg, "model", "gpt-4o-mini")).strip()
    prompt = str(_pick(cfg, "prompt", "")).strip()
    try:
        max_tokens = max(16, min(int(cfg.get("max_tokens") or DEFAULT_MAX_TOKENS), 2000))
    except (TypeError, ValueError):
        max_tokens = DEFAULT_MAX_TOKENS
    timeout = float(cfg.get("timeout_seconds") or DEFAULT_TIMEOUT_S)

    if not api_key:
        raise ValueError("未配置 AI API Key（请在「AI 内容」面板填写 API Key）")
    if not base_url.startswith("http"):
        raise ValueError(f"API 接口地址格式不正确: {base_url!r}（应以 http/https 开头）")
    if not model:
        raise ValueError("未配置模型名称")

    url = base_url + "/chat/completions"
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt or "用一句话给抖音好友发条续火花的问候，10-20字。"},
        ],
        "max_tokens": max_tokens,
    }
    if cfg.get("temperature") is not None:
        # 兼容旧配置里已保存的温度；新前端已不采集，不传则用各 API 默认（部分思考型模型对 temperature 敏感）
        try:
            body["temperature"] = max(0.0, min(float(cfg["temperature"]), 2.0))
        except (TypeError, ValueError):
            pass
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + api_key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        raise ValueError(f"AI 接口返回 HTTP {exc.code}：{detail}（若 401/403 检查 API Key 是否正确；若 400 检查模型名是否正确）") from exc
    except urllib.error.URLError as exc:
        raise ValueError(f"无法连接 AI 接口（{exc.reason}），请检查接口地址或网络") from exc
    except TimeoutError as exc:
        raise ValueError("调用 AI 接口超时，请检查网络或调大 timeout") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise ValueError(f"AI 接口返回的不是 JSON（可能是网关拦截页），响应前 150 字：{raw[:150]!r}") from None

    message = {}
    try:
        message = (data.get("choices") or [{}])[0].get("message", {}) or {}
    except Exception:
        pass
    text = ""
    try:
        text = str(message.get("content") or "").strip()
    except Exception:
        text = ""

    if not text:
        reasoning = ""
        try:
            reasoning = str(message.get("reasoning_content") or message.get("reasoning") or "").strip()
        except Exception:
            reasoning = ""
        # 思考型模型把输出额度耗尽时只有思考内容、没有最终回复 → 给出明确修复提示
        if reasoning:
            raise ValueError(
                "模型只输出了思考过程、没有最终回复（AI 返回内容为空）——这是思考型模型把 max_tokens 用完了，"
                "请在面板把「最大输出 Tokens」调大（如 512），或换非思考模型（如 glm-4-flash 旧版 / Qwen2.5-7B-Instruct）"
            )
        raise ValueError(
            "AI 返回内容为空：模型没有产出文字。请检查：① 模型名是否正确（注意大小写）② 在面板把「最大输出 Tokens」调大 ③ 换一个模型试试"
        )
    return text
