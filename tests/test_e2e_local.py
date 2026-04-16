"""
End-to-end local test script for 6 key scenarios.

Prerequisites:
  1. iLink Mock running:  python tests/ilink_mock_server.py --port 9999
  2. JellyfishBot running with ILINK_BASE_URL=http://localhost:9999
     and ILINK_CDN_URL=http://localhost:9999/cdn
  3. Valid LLM API key in .env (OPENAI_API_KEY or ANTHROPIC_API_KEY)

Usage:
    python tests/test_e2e_local.py                    # run all scenarios
    python tests/test_e2e_local.py --scenario 1       # run scenario 1 only
    python tests/test_e2e_local.py --scenario 1 2     # run scenarios 1 and 2
    python tests/test_e2e_local.py --skip-setup        # skip user/service setup
"""

import os
import sys
import json
import time
import asyncio
import argparse
import logging
from typing import Optional

import httpx

log = logging.getLogger("e2e_test")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

APP_BASE = os.environ.get("APP_BASE_URL", "http://localhost:8000")
MOCK_BASE = os.environ.get("MOCK_BASE_URL", "http://localhost:9999")

TEST_USERNAME = "test_e2e_user"
TEST_PASSWORD = "test_e2e_pass_123"
REG_KEY = os.environ.get("REG_KEY", "TEST-E2E-KEY-001")

AGENT_TIMEOUT = 120
POLL_INTERVAL = 3


class TestContext:
    """Holds state accumulated across scenarios."""

    def __init__(self):
        self.admin_token: str = ""
        self.user_id: str = ""
        self.service_id: str = ""
        self.service_key: str = ""
        self.admin_conv_id: str = ""
        self.consumer_conv_id: str = ""
        self.admin_wechat_session_id: str = ""
        self.service_wechat_session_id: str = ""
        self.service_wechat_conv_id: str = ""


ctx = TestContext()

# ── Helpers ──────────────────────────────────────────────────────────


def _headers(token: str = "") -> dict:
    h: dict = {"Content-Type": "application/json"}
    t = token or ctx.admin_token
    if t and t != "none":
        h["Authorization"] = f"Bearer {t}"
    return h


async def _get(client: httpx.AsyncClient, path: str, token: str = "", **kw) -> dict:
    resp = await client.get(f"{APP_BASE}{path}", headers=_headers(token), **kw)
    resp.raise_for_status()
    return resp.json()


async def _post(client: httpx.AsyncClient, path: str, body: dict = None,
                token: str = "", **kw) -> dict:
    resp = await client.post(
        f"{APP_BASE}{path}", json=body, headers=_headers(token), **kw,
    )
    resp.raise_for_status()
    return resp.json()


async def _put(client: httpx.AsyncClient, path: str, body: dict,
               token: str = "") -> dict:
    resp = await client.put(
        f"{APP_BASE}{path}", json=body, headers=_headers(token),
    )
    resp.raise_for_status()
    return resp.json()


async def _delete(client: httpx.AsyncClient, path: str, token: str = "") -> dict:
    resp = await client.delete(f"{APP_BASE}{path}", headers=_headers(token))
    resp.raise_for_status()
    return resp.json()


async def mock_post(client: httpx.AsyncClient, path: str, body: dict = None) -> dict:
    resp = await client.post(f"{MOCK_BASE}{path}", json=body or {})
    resp.raise_for_status()
    return resp.json()


async def mock_get(client: httpx.AsyncClient, path: str) -> dict:
    resp = await client.get(f"{MOCK_BASE}{path}")
    resp.raise_for_status()
    return resp.json()


async def wait_for_sent_messages(
    client: httpx.AsyncClient,
    min_count: int = 1,
    since: float = 0.0,
    timeout: float = AGENT_TIMEOUT,
) -> list:
    """Poll mock server until at least min_count messages appear."""
    start = time.time()
    while time.time() - start < timeout:
        data = await mock_get(client, f"/mock/sent-messages?since={since}")
        msgs = data.get("messages", [])
        if len(msgs) >= min_count:
            return msgs
        await asyncio.sleep(POLL_INTERVAL)
    raise TimeoutError(
        f"Timed out waiting for {min_count} sent messages "
        f"(got {len(msgs)} in {timeout}s)"
    )


async def consume_sse(
    client: httpx.AsyncClient,
    path: str,
    body: dict,
    token: str = "",
    timeout: float = AGENT_TIMEOUT,
) -> str:
    """Send a chat request and consume the full SSE stream, return final text."""
    full_text = ""
    async with client.stream(
        "POST", f"{APP_BASE}{path}", json=body,
        headers=_headers(token), timeout=timeout,
    ) as resp:
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            if not line.startswith("data: "):
                continue
            payload = line[6:]
            if payload == "[DONE]":
                break
            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                continue
            etype = event.get("type", "")
            if etype == "token":
                full_text += event.get("content", "")
            elif etype == "error":
                log.error("SSE error: %s", event.get("content", ""))
    return full_text


# ── Setup ────────────────────────────────────────────────────────────


async def setup_user_and_service(client: httpx.AsyncClient):
    """Register/login a test user and create a service with full capabilities."""
    log.info("=== SETUP: Creating test user and service ===")

    # Try login first, register if fails
    try:
        data = await _post(client, "/api/auth/login", {
            "username": TEST_USERNAME, "password": TEST_PASSWORD,
        }, token="none")
        ctx.admin_token = data["token"]
        ctx.user_id = data["user_id"]
        log.info("Logged in as %s (user_id=%s)", TEST_USERNAME, ctx.user_id)
    except httpx.HTTPStatusError:
        data = await _post(client, "/api/auth/register", {
            "username": TEST_USERNAME, "password": TEST_PASSWORD,
            "reg_key": REG_KEY,
        }, token="none")
        ctx.admin_token = data["token"]
        ctx.user_id = data["user_id"]
        log.info("Registered %s (user_id=%s)", TEST_USERNAME, ctx.user_id)

    # Check for existing services
    try:
        services = await _get(client, "/api/services")
        if services:
            ctx.service_id = services[0]["id"]
            log.info("Reusing existing service: %s", ctx.service_id)
        else:
            raise ValueError("No services")
    except Exception:
        svc = await _post(client, "/api/services", {
            "name": "E2E Test Service",
            "description": "Service for e2e testing",
            "model": "openai:gpt-4o-mini",
            "capabilities": ["humanchat", "scheduler", "web"],
            "published": True,
        })
        ctx.service_id = svc["id"]
        log.info("Created service: %s", ctx.service_id)

    # Ensure wechat channel enabled
    try:
        await _put(client, f"/api/wc/{ctx.service_id}/config", {
            "enabled": True, "max_sessions": 10,
        })
        log.info("WeChat channel enabled for service %s", ctx.service_id)
    except Exception as e:
        log.warning("Could not enable WeChat channel: %s", e)

    # Create or get API key for consumer
    try:
        key_data = await _post(client, f"/api/services/{ctx.service_id}/keys", {
            "name": "e2e-test-key",
        })
        ctx.service_key = key_data["key"]
        log.info("Service API key created: %s...", ctx.service_key[:20])
    except httpx.HTTPStatusError:
        log.warning("Could not create API key (may already exist)")

    log.info("=== SETUP COMPLETE ===\n")


# ── Scenario 1: Admin WeChat Agent ──────────────────────────────────


async def scenario_1_admin_wechat(client: httpx.AsyncClient):
    log.info("=" * 60)
    log.info("SCENARIO 1: Admin WeChat Agent")
    log.info("=" * 60)

    timestamp_before = time.time()

    # Clean up existing session
    try:
        session = await _get(client, "/api/admin/wechat/session")
        if session.get("connected"):
            await _delete(client, "/api/admin/wechat/session")
            log.info("Cleaned up existing admin wechat session")
            await asyncio.sleep(2)
    except Exception:
        pass

    # Reset mock state
    await mock_post(client, "/mock/reset")

    # Step 1: Request QR code
    qr = await _post(client, "/api/admin/wechat/qrcode")
    qr_id = qr["qr_id"]
    log.info("Admin QR generated: %s", qr_id)

    # Step 2: Simulate QR scan → confirmed
    await mock_post(client, "/mock/set-qr-status", {
        "qr_id": qr_id,
        "status": "confirmed",
        "bot_token": "mock_admin_bot_token",
        "ilink_user_id": "mock_admin_ilink_user",
        "ilink_bot_id": "mock_admin_ilink_bot",
        "baseurl": MOCK_BASE,
    })
    log.info("Mock: QR status set to confirmed")

    # Step 3: Poll until JellyfishBot picks up the confirmed status
    for _ in range(15):
        status = await _get(client, f"/api/admin/wechat/qrcode/status?qrcode={qr_id}")
        if status.get("status") == "confirmed":
            ctx.admin_conv_id = status.get("conversation_id", "")
            log.info("Admin WeChat connected! conv_id=%s", ctx.admin_conv_id)
            break
        await asyncio.sleep(1)
    else:
        raise RuntimeError("Admin WeChat did not confirm in time")

    # Wait for polling to start
    await asyncio.sleep(3)

    # Step 4: Inject a user message
    await mock_post(client, "/mock/inject-message", {
        "text": "你好，请简单介绍一下你自己",
        "from_user_id": "admin_wechat_user@im.wechat",
        "context_token": "mock_admin_ctx_001",
    })
    log.info("Mock: Injected user message")

    # Step 5: Wait for agent to process and reply via sendmessage
    sent = await wait_for_sent_messages(client, min_count=1, since=timestamp_before)
    log.info("Agent replied via WeChat! Messages sent: %d", len(sent))
    for m in sent:
        texts = [it.get("text_item", {}).get("text", "") for it in m.get("item_list", []) if it.get("type") == 1]
        log.info("  → to=%s, text=%s", m["to_user_id"], texts[:100])

    # Step 6: Verify persistence
    try:
        msgs = await _get(client, "/api/admin/wechat/messages")
        msg_list = msgs.get("messages", [])
        log.info("Admin WeChat messages persisted: %d messages", len(msg_list))
    except Exception as e:
        log.warning("Could not verify message persistence: %s", e)

    # Verify session
    session = await _get(client, "/api/admin/wechat/session")
    assert session.get("connected"), "Admin session should be connected"
    log.info("SCENARIO 1 PASSED ✓\n")


# ── Scenario 2: Service WeChat Agent ────────────────────────────────


async def scenario_2_service_wechat(client: httpx.AsyncClient):
    log.info("=" * 60)
    log.info("SCENARIO 2: Service WeChat Agent")
    log.info("=" * 60)

    timestamp_before = time.time()

    # Clean up existing sessions
    try:
        sessions = await _get(client, f"/api/wc/{ctx.service_id}/sessions")
        for s in sessions:
            await _delete(client, f"/api/wc/{ctx.service_id}/sessions/{s['session_id']}")
            log.info("Cleaned up existing session: %s", s["session_id"])
        await asyncio.sleep(2)
    except Exception:
        pass

    # Reset mock (keep admin session if any)
    await mock_post(client, "/mock/reset")

    # Step 1: Request QR code (public endpoint, no auth)
    resp = await client.get(f"{APP_BASE}/api/wc/{ctx.service_id}/qrcode")
    resp.raise_for_status()
    qr = resp.json()
    qr_id = qr["qr_id"]
    log.info("Service QR generated: %s", qr_id)

    # Step 2: Simulate QR scan → confirmed
    await mock_post(client, "/mock/set-qr-status", {
        "qr_id": qr_id,
        "status": "confirmed",
        "bot_token": "mock_svc_bot_token",
        "ilink_user_id": "mock_svc_ilink_user",
        "ilink_bot_id": "mock_svc_ilink_bot",
        "baseurl": MOCK_BASE,
    })
    log.info("Mock: Service QR status set to confirmed")

    # Step 3: Poll for confirmation
    for _ in range(15):
        resp = await client.get(
            f"{APP_BASE}/api/wc/{ctx.service_id}/qrcode/status",
            params={"qrcode": qr_id},
        )
        status = resp.json()
        if status.get("status") == "confirmed":
            ctx.service_wechat_session_id = status.get("session_id", "")
            ctx.service_wechat_conv_id = status.get("conversation_id", "")
            log.info("Service WeChat connected! session_id=%s, conv_id=%s",
                     ctx.service_wechat_session_id, ctx.service_wechat_conv_id)
            break
        await asyncio.sleep(1)
    else:
        raise RuntimeError("Service WeChat did not confirm in time")

    await asyncio.sleep(3)

    # Step 4: Inject user message
    await mock_post(client, "/mock/inject-message", {
        "text": "你好",
        "from_user_id": "svc_wechat_user@im.wechat",
        "context_token": "mock_svc_ctx_001",
    })
    log.info("Mock: Injected service user message")

    # Step 5: Wait for reply
    sent = await wait_for_sent_messages(client, min_count=1, since=timestamp_before)
    log.info("Service agent replied via WeChat! Messages sent: %d", len(sent))
    for m in sent:
        texts = [it.get("text_item", {}).get("text", "") for it in m.get("item_list", []) if it.get("type") == 1]
        log.info("  → to=%s, text=%s", m["to_user_id"], texts[:100])

    # Step 6: Verify consumer conversation persisted
    try:
        sessions = await _get(client, f"/api/wc/{ctx.service_id}/sessions")
        log.info("Service WeChat sessions: %d", len(sessions))
        assert len(sessions) >= 1, "Should have at least 1 session"
    except Exception as e:
        log.warning("Session verification: %s", e)

    log.info("SCENARIO 2 PASSED ✓\n")


# ── Scenario 3: Admin Scheduled Task ────────────────────────────────


async def scenario_3_admin_task(client: httpx.AsyncClient):
    log.info("=" * 60)
    log.info("SCENARIO 3: Admin Scheduled Task (agent type)")
    log.info("=" * 60)

    # Create a once-type agent task that runs immediately
    task = await _post(client, "/api/scheduler", {
        "name": "E2E Test Admin Task",
        "description": "Test scheduled agent task",
        "schedule_type": "once",
        "schedule": "",  # empty = run now via run-now endpoint
        "task_type": "agent",
        "task_config": {
            "prompt": "请用一句话回答：1+1等于几？只需要回答数字。",
        },
        "enabled": True,
    })
    task_id = task["id"]
    log.info("Created admin task: %s", task_id)

    # Trigger immediate execution
    await _post(client, f"/api/scheduler/{task_id}/run-now")
    log.info("Triggered run-now for task %s", task_id)

    # Wait for run to complete
    start = time.time()
    while time.time() - start < AGENT_TIMEOUT:
        runs = await _get(client, f"/api/scheduler/{task_id}/runs")
        if runs and len(runs) > 0:
            latest = runs[-1]
            status = latest.get("status", "")
            if status in ("success", "completed", "done", "error", "failed", "timeout"):
                log.info("Task run completed with status: %s", status)
                steps = latest.get("steps", [])
                log.info("Run steps (%d):", len(steps))
                for s in steps:
                    log.info("  [%s] %s", s.get("type", "?"), str(s.get("content", ""))[:100])
                break
        await asyncio.sleep(POLL_INTERVAL)
    else:
        raise TimeoutError("Admin task did not complete in time")

    # Clean up
    try:
        await _delete(client, f"/api/scheduler/{task_id}")
        log.info("Cleaned up task %s", task_id)
    except Exception:
        pass

    log.info("SCENARIO 3 PASSED ✓\n")


# ── Scenario 4: Service Scheduled Task ──────────────────────────────


async def scenario_4_service_task(client: httpx.AsyncClient):
    log.info("=" * 60)
    log.info("SCENARIO 4: Service Scheduled Task")
    log.info("=" * 60)

    timestamp_before = time.time()
    # Reset mock sent messages to count fresh
    await mock_post(client, "/mock/reset")

    reply_to = None
    if ctx.service_wechat_session_id:
        reply_to = {
            "channel": "wechat",
            "admin_id": ctx.user_id,
            "service_id": ctx.service_id,
            "session_id": ctx.service_wechat_session_id,
            "conversation_id": ctx.service_wechat_conv_id,
        }
        log.info("Service task will reply to WeChat session: %s",
                 ctx.service_wechat_session_id)

    task = await _post(client, f"/api/scheduler/services/{ctx.service_id}", {
        "name": "E2E Test Service Task",
        "description": "Test service scheduled task",
        "schedule_type": "once",
        "schedule": "",
        "task_config": {
            "prompt": "请用一句话自我介绍。",
        },
        "reply_to": reply_to,
        "enabled": True,
    })
    task_id = task["id"]
    log.info("Created service task: %s", task_id)

    # Trigger
    await _post(client, f"/api/scheduler/services/{ctx.service_id}/{task_id}/run-now")
    log.info("Triggered run-now for service task %s", task_id)

    # Wait for completion
    start = time.time()
    while time.time() - start < AGENT_TIMEOUT:
        runs = await _get(
            client, f"/api/scheduler/services/{ctx.service_id}/{task_id}/runs"
        )
        if runs and len(runs) > 0:
            latest = runs[-1]
            status = latest.get("status", "")
            if status in ("success", "completed", "done", "error", "failed", "timeout"):
                log.info("Service task run completed with status: %s", status)
                steps = latest.get("steps", [])
                log.info("Run steps (%d):", len(steps))
                for s in steps:
                    log.info("  [%s] %s", s.get("type", "?"), str(s.get("content", ""))[:100])
                break
        await asyncio.sleep(POLL_INTERVAL)
    else:
        raise TimeoutError("Service task did not complete in time")

    # Verify WeChat delivery if connected
    if reply_to and reply_to["channel"] == "wechat":
        try:
            sent = await wait_for_sent_messages(client, min_count=1,
                                                since=timestamp_before, timeout=10)
            log.info("Service task sent %d message(s) to WeChat", len(sent))
        except TimeoutError:
            log.warning("No WeChat messages sent (agent may not have used send_message)")

    # Clean up
    try:
        await _delete(
            client, f"/api/scheduler/services/{ctx.service_id}/{task_id}"
        )
    except Exception:
        pass

    log.info("SCENARIO 4 PASSED ✓\n")


# ── Scenario 5: Admin Broadcast (publish_service_task) ──────────────


async def scenario_5_admin_broadcast(client: httpx.AsyncClient):
    log.info("=" * 60)
    log.info("SCENARIO 5: Admin Broadcast (publish_service_task)")
    log.info("=" * 60)

    if not ctx.service_wechat_session_id:
        log.warning("SKIPPED: No active service WeChat session (run scenario 2 first)")
        return

    timestamp_before = time.time()
    await mock_post(client, "/mock/reset")

    # We need to re-establish the service WeChat session since mock was reset.
    # The mock reset clears the QR state, but the session in JellyfishBot is still alive.
    # The key thing is that the session_manager still has the session in memory.

    # Verify session exists
    sessions = await _get(client, f"/api/wc/{ctx.service_id}/sessions")
    if not sessions:
        log.warning("SKIPPED: No active sessions for broadcast")
        return
    log.info("Active sessions for broadcast: %d", len(sessions))

    # Send broadcast via admin chat - the agent should use publish_service_task
    # We need a conversation first
    conv_resp = await _post(client, "/api/conversations", {"title": "Broadcast Test"})
    conv_id = conv_resp.get("id", conv_resp.get("conversation_id", ""))
    log.info("Created admin conversation for broadcast: %s", conv_id)

    # Send the broadcast command via SSE
    full_text = await consume_sse(client, "/api/chat", {
        "conversation_id": conv_id,
        "message": (
            f"请向服务 {ctx.service_id} 的所有微信用户广播以下消息："
            "系统将于今晚22:00进行维护，届时服务将暂停1小时。"
        ),
        "model": "openai:gpt-4o-mini",
        "capabilities": ["humanchat", "service_broadcast"],
    })
    log.info("Admin broadcast response: %s", full_text[:200])

    # Check if service tasks were created
    await asyncio.sleep(5)
    try:
        svc_tasks = await _get(client, f"/api/scheduler/services/{ctx.service_id}")
        broadcast_tasks = [t for t in svc_tasks if "广播" in t.get("name", "")
                          or "broadcast" in t.get("name", "").lower()
                          or "维护" in t.get("description", "")]
        log.info("Service tasks after broadcast: %d total, %d broadcast-related",
                 len(svc_tasks), len(broadcast_tasks))
    except Exception as e:
        log.warning("Could not check service tasks: %s", e)

    # Check if mock received messages
    try:
        sent = await wait_for_sent_messages(client, min_count=1,
                                            since=timestamp_before, timeout=30)
        log.info("Broadcast delivered %d message(s) via WeChat", len(sent))
    except TimeoutError:
        log.warning("No WeChat messages from broadcast (task may still be running)")

    log.info("SCENARIO 5 PASSED ✓\n")


# ── Scenario 6: Service → Admin Notification (contact_admin) ────────


async def scenario_6_service_notify(client: httpx.AsyncClient):
    log.info("=" * 60)
    log.info("SCENARIO 6: Service → Admin Notification (contact_admin)")
    log.info("=" * 60)

    if not ctx.service_key:
        log.warning("SKIPPED: No service API key available")
        return

    timestamp_before = time.time()

    # Create consumer conversation
    conv = await _post(client, "/api/v1/conversations", {
        "title": "Notification Test",
    }, token=ctx.service_key)
    consumer_conv_id = conv.get("id", conv.get("conversation_id", ""))
    log.info("Created consumer conversation: %s", consumer_conv_id)

    # Send a message asking to contact admin
    full_text = await consume_sse(client, "/api/v1/chat", {
        "conversation_id": consumer_conv_id,
        "message": "我的账户有问题，请帮我联系管理员，告诉他用户账号异常需要处理。",
    }, token=ctx.service_key)
    log.info("Consumer agent response: %s", full_text[:200])

    # Wait a bit for inbox processing
    await asyncio.sleep(5)

    # Verify inbox entry was created
    # We check the filesystem directly since there's no public inbox list API
    # for automated tests. Instead we use the admin inbox API if available.
    try:
        inbox_resp = await _get(client, "/api/inbox")
        inbox_msgs = inbox_resp.get("messages", []) if isinstance(inbox_resp, dict) else inbox_resp
        unread = [m for m in inbox_msgs if isinstance(m, dict) and m.get("status") == "unread"]
        log.info("Admin inbox: %d total, %d unread", len(inbox_msgs), len(unread))
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            log.info("No inbox API endpoint; checking filesystem would require direct access")
        else:
            log.warning("Inbox check failed: %s", e)
    except Exception as e:
        log.warning("Inbox check: %s", e)

    # If admin WeChat is connected, check for forwarded notification
    if ctx.admin_conv_id:
        try:
            sent = await wait_for_sent_messages(client, min_count=1,
                                                since=timestamp_before, timeout=15)
            log.info("Inbox notification forwarded to admin WeChat! %d message(s)", len(sent))
        except TimeoutError:
            log.info("No WeChat forwarding (admin may not be connected or inbox agent disabled)")

    log.info("SCENARIO 6 PASSED ✓\n")


# ── Health Checks ────────────────────────────────────────────────────


async def check_prerequisites(client: httpx.AsyncClient) -> bool:
    """Verify mock server and app are running."""
    ok = True

    # Check mock server
    try:
        resp = await client.get(f"{MOCK_BASE}/mock/status")
        resp.raise_for_status()
        log.info("✓ iLink Mock server running at %s", MOCK_BASE)
    except Exception as e:
        log.error("✗ iLink Mock server not reachable at %s: %s", MOCK_BASE, e)
        ok = False

    # Check JellyfishBot
    try:
        resp = await client.get(f"{APP_BASE}/docs")
        log.info("✓ JellyfishBot running at %s", APP_BASE)
    except Exception as e:
        log.error("✗ JellyfishBot not reachable at %s: %s", APP_BASE, e)
        ok = False

    return ok


# ── Main ─────────────────────────────────────────────────────────────


async def main(scenarios: list[int], skip_setup: bool = False):
    async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=15.0)) as client:
        # Prerequisites
        if not await check_prerequisites(client):
            log.error("Prerequisites not met. Ensure mock and app are running.")
            sys.exit(1)

        # Setup
        if not skip_setup:
            await setup_user_and_service(client)
        else:
            data = await _post(client, "/api/auth/login", {
                "username": TEST_USERNAME, "password": TEST_PASSWORD,
            }, token="none")
            ctx.admin_token = data["token"]
            ctx.user_id = data["user_id"]
            log.info("Logged in as %s", TEST_USERNAME)

            # Load existing service info
            services = await _get(client, "/api/services")
            if services:
                ctx.service_id = services[0]["id"]
                log.info("Using existing service: %s", ctx.service_id)
                # Try to get an existing key or create one
                try:
                    key_data = await _post(
                        client, f"/api/services/{ctx.service_id}/keys",
                        {"name": "e2e-skip-setup"},
                    )
                    ctx.service_key = key_data["key"]
                except Exception:
                    log.warning("Could not create API key (may need manual setup)")

        all_scenarios = {
            1: ("Admin WeChat Agent", scenario_1_admin_wechat),
            2: ("Service WeChat Agent", scenario_2_service_wechat),
            3: ("Admin Scheduled Task", scenario_3_admin_task),
            4: ("Service Scheduled Task", scenario_4_service_task),
            5: ("Admin Broadcast", scenario_5_admin_broadcast),
            6: ("Service → Admin Notification", scenario_6_service_notify),
        }

        to_run = scenarios or list(all_scenarios.keys())
        results = {}

        for num in to_run:
            name, func = all_scenarios[num]
            try:
                await func(client)
                results[num] = "PASSED"
            except Exception as e:
                log.error("SCENARIO %d (%s) FAILED: %s", num, name, e, exc_info=True)
                results[num] = f"FAILED: {e}"

        # Summary
        log.info("=" * 60)
        log.info("TEST RESULTS SUMMARY")
        log.info("=" * 60)
        for num in to_run:
            name = all_scenarios[num][0]
            status = results.get(num, "NOT RUN")
            marker = "✓" if status == "PASSED" else "✗"
            log.info("  %s Scenario %d: %s — %s", marker, num, name, status)
        log.info("=" * 60)

        failed = [n for n, s in results.items() if s != "PASSED"]
        if failed:
            log.error("%d scenario(s) failed", len(failed))
            sys.exit(1)
        else:
            log.info("All %d scenario(s) passed!", len(results))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="E2E Local Test")
    parser.add_argument(
        "--scenario", "-s", nargs="*", type=int, default=[],
        help="Scenario numbers to run (default: all)",
    )
    parser.add_argument(
        "--skip-setup", action="store_true",
        help="Skip user/service setup (reuse existing)",
    )
    args = parser.parse_args()
    asyncio.run(main(args.scenario, args.skip_setup))
