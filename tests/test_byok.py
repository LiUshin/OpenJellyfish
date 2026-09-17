"""BYOK（调用方自付 LLM）单测 —— 无需启动服务、无需真实上游。

覆盖：
  1. byok.resolve_request_credentials 的白名单 / 协议 / provider 一致性校验
  2. service key 的 billing 存储与 verify_service_key 回读
  3. consumer 路由的 hosted / byok 前置分支与 /api/v1/models 发现端点

用法：
    .venv/bin/python tests/test_byok.py
"""

import asyncio
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import HTTPException  # noqa: E402

from app.services import byok  # noqa: E402

ANTHROPIC_MODEL = "anthropic:claude-sonnet-4-5-20250929"
ADMIN_ID = "byok_test_admin"
SVC_CONFIG = {"model": ANTHROPIC_MODEL, "capabilities": [], "published": True}

_passed = 0


def check(label: str, cond: bool) -> None:
    global _passed
    if not cond:
        raise AssertionError(f"FAIL: {label}")
    _passed += 1
    print(f"  ok  {label}")


def expect_error(label: str, fn, needle: str = "") -> None:
    try:
        fn()
    except byok.BYOKError as e:
        check(f"{label} → {e}", needle in str(e) if needle else True)
        return
    raise AssertionError(f"FAIL: {label} —— 预期 BYOKError，实际没抛")


def expect_http_400(label: str, fn, needle: str = "") -> None:
    try:
        fn()
    except HTTPException as e:
        ok = e.status_code == 400 and (needle in str(e.detail) if needle else True)
        check(f"{label} → 400 {e.detail}", ok)
        return
    raise AssertionError(f"FAIL: {label} —— 预期 HTTPException(400)，实际没抛")


def resolve(**kw):
    base = dict(admin_id=ADMIN_ID, svc_config=SVC_CONFIG,
                provider="anthropic", api_key="sk-caller-secret",
                model=ANTHROPIC_MODEL)
    base.update(kw)
    return byok.resolve_request_credentials(**base)


# ── 1. 凭据校验 ───────────────────────────────────────────────────────

def test_resolve_credentials():
    print("\n[1] resolve_request_credentials")

    model_id, creds = resolve()
    check("默认 base_url 落到 provider 默认值",
          model_id == ANTHROPIC_MODEL
          and creds == {"api_key": "sk-caller-secret",
                        "base_url": "https://api.anthropic.com"})

    _, creds = resolve(base_url="https://api.anthropic.com/v1/")
    check("白名单内的 base_url 被接受且去掉尾斜杠",
          creds["base_url"] == "https://api.anthropic.com/v1")

    _, creds = resolve(model=None)
    check("不传 model 时回落服务配置的默认模型",
          creds["base_url"] == "https://api.anthropic.com")

    expect_error("缺 api_key", lambda: resolve(api_key=""), "自带凭据")
    expect_error("缺 provider", lambda: resolve(provider=""), "缺少 provider")
    expect_error("provider 与 model 厂商不一致",
                 lambda: resolve(provider="openai"), "不一致")
    expect_error("model 不在 catalog 白名单",
                 lambda: resolve(model="anthropic:not-a-real-model"), "不在")
    expect_error("http base_url 被拒",
                 lambda: resolve(base_url="http://api.anthropic.com"), "https")
    expect_error("内网 host 被拒",
                 lambda: resolve(base_url="https://169.254.169.254/latest"), "白名单")
    expect_error("localhost 被拒",
                 lambda: resolve(base_url="https://127.0.0.1/v1"), "白名单")
    expect_error("非 443 端口被拒",
                 lambda: resolve(base_url="https://api.anthropic.com:8443"), "443")
    expect_error("base_url 带用户名密码被拒",
                 lambda: resolve(base_url="https://u:p@api.anthropic.com"), "用户名")
    expect_error("非 bedrock 传 region 被拒",
                 lambda: resolve(region="us-east-1"), "region")

    os.environ["BYOK_EXTRA_ALLOWED_HOSTS"] = "anthropic=gw.example.com"
    try:
        _, creds = resolve(base_url="https://gw.example.com/v1")
        check("env 追加的 host 生效", creds["base_url"] == "https://gw.example.com/v1")
        expect_error("env 追加的 host 只对指定 provider 生效",
                     lambda: resolve(provider="openai", model="openai:gpt-4o",
                                     base_url="https://gw.example.com/v1"),
                     "白名单")
    finally:
        del os.environ["BYOK_EXTRA_ALLOWED_HOSTS"]

    fp1 = byok.credentials_fingerprint(ANTHROPIC_MODEL, {"api_key": "a", "base_url": "b"})
    fp2 = byok.credentials_fingerprint(ANTHROPIC_MODEL, {"api_key": "z", "base_url": "b"})
    check("不同 api_key 产生不同指纹", fp1 != fp2)
    check("指纹不含明文 key", "a" not in fp1 or len(fp1) == 16)


# ── 2. Key 的 billing 存储 ────────────────────────────────────────────

def test_key_billing():
    print("\n[2] service key billing")
    from app.core import security
    from app.services import published

    tmp = tempfile.mkdtemp(prefix="byok-users-")
    original = security.USERS_DIR
    security.USERS_DIR = tmp
    try:
        svc = published.create_service(ADMIN_ID, dict(SVC_CONFIG, name="t"))
        sid = svc["id"]

        hosted = published.create_service_key(ADMIN_ID, sid, "hosted-key")
        byok_key = published.create_service_key(ADMIN_ID, sid, "byok-key", billing="byok")
        legacy = published.create_service_key(ADMIN_ID, sid, "legacy", billing="nonsense")

        check("hosted key 仍是 sk-svc- 前缀", hosted["key"].startswith("sk-svc-"))
        check("byok key 用 sk-byok- 前缀", byok_key["key"].startswith("sk-byok-"))
        check("非法 billing 归一为 hosted", legacy["billing"] == "hosted")

        listed = {k["name"]: k["billing"] for k in published.list_service_keys(ADMIN_ID, sid)}
        check("列表返回 billing", listed == {"hosted-key": "hosted",
                                            "byok-key": "byok",
                                            "legacy": "hosted"})

        ctx = published.verify_service_key(byok_key["key"])
        check("verify_service_key 回读 byok", ctx and ctx["billing"] == "byok")
        ctx = published.verify_service_key(hosted["key"])
        check("verify_service_key 回读 hosted", ctx and ctx["billing"] == "hosted")

        # 旧 keys.json（无 billing 字段）必须按 hosted 处理，否则老客户会被要求自带凭据。
        check("缺失字段归一为 hosted", published.normalize_billing(None) == "hosted")
    finally:
        security.USERS_DIR = original
        shutil.rmtree(tmp, ignore_errors=True)


# ── 3. 路由前置分支 ───────────────────────────────────────────────────

class FakeReq:
    def __init__(self, **kw):
        for f in ("provider", "api_key", "base_url", "model", "region"):
            setattr(self, f, kw.get(f))


def test_routes():
    print("\n[3] consumer 路由分支")
    from app.routes.consumer import _resolve_billing, api_consumer_models

    hosted_ctx = {"admin_id": ADMIN_ID, "service_id": "svc_x",
                  "billing": "hosted", "service_config": SVC_CONFIG}
    byok_ctx = dict(hosted_ctx, billing="byok")

    check("hosted 无凭据字段 → 不改动行为",
          _resolve_billing(hosted_ctx, FakeReq()) == (None, None))
    check("hosted 带 model 仍放行（OpenAI SDK 总会带 model）",
          _resolve_billing(hosted_ctx, FakeReq(model="gpt-4o")) == (None, None))
    expect_http_400("hosted 带 api_key",
                    lambda: _resolve_billing(hosted_ctx, FakeReq(api_key="sk-x")),
                    "运营方付费")
    expect_http_400("byok 缺 api_key",
                    lambda: _resolve_billing(byok_ctx, FakeReq(provider="anthropic")),
                    "自带凭据")
    expect_http_400("byok base_url 越界",
                    lambda: _resolve_billing(byok_ctx, FakeReq(
                        provider="anthropic", api_key="sk-x",
                        model=ANTHROPIC_MODEL, base_url="https://evil.example.com")),
                    "白名单")

    model_id, creds = _resolve_billing(byok_ctx, FakeReq(
        provider="anthropic", api_key="sk-caller", model=ANTHROPIC_MODEL))
    check("byok 正常路径返回调用方凭据",
          model_id == ANTHROPIC_MODEL and creds["api_key"] == "sk-caller")

    info = asyncio.run(api_consumer_models(ctx=hosted_ctx))
    check("hosted 的 /models 不暴露模型白名单",
          info["billing"] == "hosted" and info["models"] == [])

    info = asyncio.run(api_consumer_models(ctx=byok_ctx))
    check("byok 的 /models 返回可选模型与允许的 host",
          info["billing"] == "byok"
          and any(m["id"] == ANTHROPIC_MODEL for m in info["models"])
          and "api.anthropic.com" in info["allowed_hosts"]["anthropic"])


if __name__ == "__main__":
    test_resolve_credentials()
    test_key_billing()
    test_routes()
    print(f"\n全部通过（{_passed} 项断言）")
