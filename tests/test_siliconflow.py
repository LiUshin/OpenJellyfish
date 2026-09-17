"""硅基流动（SiliconFlow）接入单测 —— 无需启动服务、无需真实上游。

覆盖：
  1. thinking 参数构造（enable_thinking 只发给混合思考模型 / thinking_budget 夹取）
  2. 凭据解析（平台 env vs Admin 自己的 key、base_url 默认值）
  3. 白名单读写与归一化（前缀剥离、去重、budget 校验）
  4. catalog synthetic 条目（list_models / find_model / 与 OpenRouter 并存）
  5. BYOK host 白名单

用法：
    .venv/bin/python tests/test_siliconflow.py
"""

import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.security import get_user_dir  # noqa: E402
from app.services import byok, siliconflow  # noqa: E402

USER_ID = "siliconflow_test_admin"

_passed = 0


def check(label: str, cond: bool) -> None:
    global _passed
    if not cond:
        raise AssertionError(f"FAIL: {label}")
    _passed += 1
    print(f"  ok  {label}")


def clear_env(*names: str) -> None:
    for n in names:
        os.environ.pop(n, None)


# ── 1. thinking 参数 ─────────────────────────────────────────────────

def test_extra_body():
    print("\n[1] siliconflow.build_extra_body")
    clear_env("SILICONFLOW_HYBRID_MODELS")

    check(
        "Qwen3 混合思考模型识别",
        siliconflow.supports_enable_thinking("Qwen/Qwen3-32B"),
    )
    check(
        "Pro/ 计费前缀不影响识别",
        siliconflow.supports_enable_thinking("Pro/deepseek-ai/DeepSeek-V3.2"),
    )
    check(
        "大小写不影响识别",
        siliconflow.supports_enable_thinking("qwen/QWEN3.5-35B-A3B"),
    )
    check(
        "常驻思考模型 R1 不在混合列表",
        not siliconflow.supports_enable_thinking("deepseek-ai/DeepSeek-R1"),
    )

    os.environ["SILICONFLOW_HYBRID_MODELS"] = "some-vendor/Future-Model-9"
    check(
        "env 可追加新的混合思考模型",
        siliconflow.supports_enable_thinking("some-vendor/future-model-9"),
    )
    clear_env("SILICONFLOW_HYBRID_MODELS")

    # 混合模型：开关双向都要显式发出去，关掉才是真的省 token
    check(
        "混合模型 + reasoning 开 → enable_thinking=True",
        siliconflow.build_extra_body("Qwen/Qwen3-32B", {"reasoning": True})
        == {"enable_thinking": True},
    )
    check(
        "混合模型 + reasoning 关 → enable_thinking=False",
        siliconflow.build_extra_body("Qwen/Qwen3-32B", {"reasoning": False})
        == {"enable_thinking": False},
    )
    check(
        "混合模型 + budget → 两个字段都带",
        siliconflow.build_extra_body(
            "Qwen/Qwen3-32B", {"reasoning": True, "thinking_budget": 2048},
        ) == {"enable_thinking": True, "thinking_budget": 2048},
    )

    # 非混合模型：绝不能发 enable_thinking，会被后端拒
    check(
        "非混合模型 + reasoning 关 → 什么都不发",
        siliconflow.build_extra_body("deepseek-ai/DeepSeek-R1", {"reasoning": False})
        is None,
    )
    check(
        "非混合模型 + reasoning 开 → 只发 budget",
        siliconflow.build_extra_body(
            "deepseek-ai/DeepSeek-R1", {"reasoning": True, "thinking_budget": 1024},
        ) == {"thinking_budget": 1024},
    )
    check(
        "非混合模型 + reasoning 开但无 budget → 仍不发 enable_thinking",
        siliconflow.build_extra_body("deepseek-ai/DeepSeek-R1", {"reasoning": True})
        is None,
    )

    check("budget 下越界夹到 128", siliconflow.clamp_thinking_budget(1) == 128)
    check("budget 上越界夹到 32768", siliconflow.clamp_thinking_budget(999999) == 32768)
    check("budget 非数字 → None", siliconflow.clamp_thinking_budget("4096") is None)
    check("budget True 不当成 1", siliconflow.clamp_thinking_budget(True) is None)


# ── 2. 凭据 ──────────────────────────────────────────────────────────

def test_credentials():
    print("\n[2] api_config.get_provider_credentials('siliconflow')")
    from app.core.api_config import (
        AGGREGATORS, get_provider_credentials, has_provider, has_provider_credentials,
    )

    check("siliconflow 已登记为聚合商", "siliconflow" in AGGREGATORS)

    clear_env("SILICONFLOW_API_KEY", "SILICONFLOW_BASE_URL")
    creds = get_provider_credentials("siliconflow", user_id=None)
    check("未配置时 key 为空", creds["api_key"] == "")
    check(
        "base_url 默认国内站",
        creds["base_url"] == "https://api.siliconflow.cn/v1",
    )
    check(
        "未配置时 has_provider=False",
        not has_provider("siliconflow", user_id=None),
    )

    os.environ["SILICONFLOW_API_KEY"] = "sk-platform"
    os.environ["SILICONFLOW_BASE_URL"] = "https://api.siliconflow.com/v1/"
    creds = get_provider_credentials("siliconflow", user_id=None)
    check("读到平台 env key", creds["api_key"] == "sk-platform")
    check(
        "base_url 去掉尾斜杠",
        creds["base_url"] == "https://api.siliconflow.com/v1",
    )
    check(
        "配置后 has_provider_credentials=True",
        has_provider_credentials("siliconflow", user_id=None),
    )
    clear_env("SILICONFLOW_API_KEY", "SILICONFLOW_BASE_URL")

    # Admin 自己的 key：credential_source=user 时不回落 env
    from app.core.user_api_keys import ALL_FIELDS, save_user_api_keys
    check("siliconflow_api_key 是合法字段", "siliconflow_api_key" in ALL_FIELDS)
    check("siliconflow_base_url 是合法字段", "siliconflow_base_url" in ALL_FIELDS)

    os.environ["SILICONFLOW_API_KEY"] = "sk-platform"
    save_user_api_keys(USER_ID, {"siliconflow_api_key": "sk-mine"})
    creds = get_provider_credentials("siliconflow", user_id=USER_ID)
    check("有个人 key 时优先用个人 key", creds["api_key"] == "sk-mine")
    clear_env("SILICONFLOW_API_KEY")


# ── 3. 白名单 ────────────────────────────────────────────────────────

def test_whitelist():
    print("\n[3] preferences 白名单")
    from app.services.preferences import (
        AGGREGATOR_PROVIDERS, get_enabled_models, set_enabled_models,
    )

    check("siliconflow 在聚合商列表", "siliconflow" in AGGREGATOR_PROVIDERS)
    check("openrouter 仍在聚合商列表", "openrouter" in AGGREGATOR_PROVIDERS)

    saved = set_enabled_models(USER_ID, "siliconflow", [
        {"id": "siliconflow:Qwen/Qwen3-32B", "name": "Qwen3 32B",
         "reasoning": True, "thinking_budget": 2048},
        {"id": "Qwen/Qwen3-32B", "name": "重复项"},
        {"id": "deepseek-ai/DeepSeek-R1"},
        {"id": "  ", "name": "空 id"},
        "zai-org/GLM-4.6",
        {"id": "moonshotai/Kimi-K2", "reasoning": True, "thinking_budget": 99},
    ])
    ids = [m["id"] for m in saved]
    check("剥掉误带的 provider 前缀", "Qwen/Qwen3-32B" in ids)
    check("同 id 去重（保留首个）", ids.count("Qwen/Qwen3-32B") == 1)
    check("空 id 被丢弃", all(m["id"].strip() for m in saved))
    check("纯字符串条目也被接受", "zai-org/GLM-4.6" in ids)
    check("共 4 条", len(saved) == 4)

    by_id = {m["id"]: m for m in saved}
    check("budget 原样保留", by_id["Qwen/Qwen3-32B"]["thinking_budget"] == 2048)
    check("越界 budget 被丢弃", "thinking_budget" not in by_id["moonshotai/Kimi-K2"])
    check("无 name 时回落 id", by_id["deepseek-ai/DeepSeek-R1"]["name"] == "deepseek-ai/DeepSeek-R1")
    check("reasoning 默认 False", by_id["deepseek-ai/DeepSeek-R1"]["reasoning"] is False)

    check("回读一致", [m["id"] for m in get_enabled_models(USER_ID, "siliconflow")] == ids)
    check(
        "两家白名单互不干扰",
        get_enabled_models(USER_ID, "openrouter") == [],
    )
    check("未知 provider 返回空", get_enabled_models(USER_ID, "nope") == [])


# ── 4. catalog ───────────────────────────────────────────────────────

def test_catalog():
    print("\n[4] model_catalog synthetic 条目")
    from app.services.model_catalog import find_model, list_models
    from app.services.preferences import set_enabled_models

    set_enabled_models(USER_ID, "openrouter", [
        {"id": "anthropic/claude-sonnet-4", "name": "Sonnet 4", "reasoning": True},
    ])

    rows = {m["id"]: m for m in list_models("llm", user_id=USER_ID)}
    check("硅基流动条目进了 llm 列表", "siliconflow:Qwen/Qwen3-32B" in rows)
    check("OpenRouter 条目仍在", "openrouter:anthropic/claude-sonnet-4" in rows)
    check(
        "静态 catalog 条目未被挤掉",
        any(k.startswith("anthropic:") for k in rows),
    )

    row = rows["siliconflow:Qwen/Qwen3-32B"]
    check("provider 字段正确", row["provider"] == "siliconflow")
    check("display_name 用白名单里的名字", row["display_name"] == "Qwen3 32B")
    check("reasoning 透传", row["reasoning"] is True)
    check("budget 透传", row["thinking_budget"] == 2048)
    check("tier=thinking", row["tier"] == "thinking")
    check(
        "非 reasoning 条目 tier=high",
        rows["siliconflow:deepseek-ai/DeepSeek-R1"]["tier"] == "high",
    )

    found = find_model("siliconflow:Qwen/Qwen3-32B", user_id=USER_ID)
    check("find_model 命中白名单条目", found is not None and found["capability"] == "llm")
    check(
        "find_model 对未勾选的返回 None",
        find_model("siliconflow:not/enabled", user_id=USER_ID) is None,
    )
    check(
        "无 user_id 时不返回白名单条目",
        find_model("siliconflow:Qwen/Qwen3-32B", user_id=None) is None,
    )


# ── 5. BYOK ──────────────────────────────────────────────────────────

def test_byok():
    print("\n[5] BYOK 白名单")
    check("siliconflow 可 BYOK", "siliconflow" in byok.BYOK_PROVIDERS)
    check(
        "默认 base 与 api_config 一致",
        byok.PROVIDER_DEFAULT_BASE["siliconflow"] == siliconflow.DEFAULT_BASE_URL,
    )
    hosts = byok.allowed_hosts("siliconflow")
    check("国内站在 host 白名单", "api.siliconflow.cn" in hosts)
    check("海外站在 host 白名单", "api.siliconflow.com" in hosts)

    model_id, creds = byok.resolve_request_credentials(
        admin_id=USER_ID,
        svc_config={"model": "siliconflow:Qwen/Qwen3-32B"},
        provider="siliconflow",
        api_key="sk-caller",
        model="siliconflow:Qwen/Qwen3-32B",
    )
    check("BYOK 解析出模型", model_id == "siliconflow:Qwen/Qwen3-32B")
    check("BYOK 回落默认 base", creds["base_url"] == siliconflow.DEFAULT_BASE_URL)

    try:
        byok.resolve_request_credentials(
            admin_id=USER_ID,
            svc_config={"model": "siliconflow:Qwen/Qwen3-32B"},
            provider="siliconflow",
            api_key="sk-caller",
            model="siliconflow:Qwen/Qwen3-32B",
            base_url="https://evil.example.com/v1",
        )
    except byok.BYOKError as e:
        check(f"非白名单 host 被拒 → {e}", "白名单" in str(e))
    else:
        raise AssertionError("FAIL: 非白名单 host 应该被拒")


def main():
    try:
        test_extra_body()
        test_credentials()
        test_whitelist()
        test_catalog()
        test_byok()
    finally:
        shutil.rmtree(get_user_dir(USER_ID), ignore_errors=True)
    print(f"\n全部通过（{_passed} 项）")


if __name__ == "__main__":
    main()
