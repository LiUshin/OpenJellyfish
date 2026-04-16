"""
Web search and fetch tools.

Supports two providers (auto-detected by env vars or per-user keys):
  - CloudsWay: CLOUDSWAY_SEARCH_KEY  (self-hosted search API)
  - Tavily:    TAVILY_API_KEY

If both are set, CloudsWay is used. Either key alone works.
"""

import os
import json
from typing import Optional

import httpx

_CW_READ_URL = os.getenv("CLOUDSWAY_READ_URL", "")
_CW_SEARCH_URL = os.getenv("CLOUDSWAY_SEARCH_URL", "")

_TAVILY_SEARCH_URL = "https://api.tavily.com/search"
_TAVILY_EXTRACT_URL = "https://api.tavily.com/extract"

_TIMEOUT = 30.0


def _resolve_keys(user_id: Optional[str] = None):
    """Return (cloudsway_key, tavily_key) checking per-user config first."""
    cw_key = ""
    tv_key = ""

    if user_id:
        try:
            from app.core.user_api_keys import get_user_api_keys
            ukeys = get_user_api_keys(user_id)
            cw_key = ukeys.get("cloudsway_search_key", "")
            tv_key = ukeys.get("tavily_api_key", "")
        except Exception:
            pass

    if not cw_key:
        cw_key = os.getenv("CLOUDSWAY_SEARCH_KEY", "")
    if not tv_key:
        tv_key = os.getenv("TAVILY_API_KEY", "")

    return cw_key, tv_key


def _provider(cw_key: str, tv_key: str) -> str:
    if cw_key:
        return "cloudsway"
    if tv_key:
        return "tavily"
    return "none"


def web_fetch(url: str, mode: str = "quality", user_id: Optional[str] = None) -> dict:
    cw_key, tv_key = _resolve_keys(user_id)
    provider = _provider(cw_key, tv_key)

    if provider == "cloudsway":
        try:
            with httpx.Client(timeout=_TIMEOUT) as client:
                resp = client.post(
                    _CW_READ_URL,
                    headers={
                        "Authorization": f"Bearer {cw_key}",
                        "Content-Type": "application/json",
                    },
                    json={"url": url, "formats": ["TEXT"], "mode": mode},
                )
            if resp.status_code != 200:
                return {
                    "success": False,
                    "content": f"HTTP {resp.status_code}: {resp.text[:300]}",
                    "url": url,
                    "provider": "cloudsway",
                }
            data = resp.json()
            text = ""
            if isinstance(data, dict):
                for field in ("text", "content", "markdown", "data"):
                    val = data.get(field)
                    if isinstance(val, str) and val.strip():
                        text = val
                        break
                    if isinstance(val, dict):
                        inner = val.get("text") or val.get("content") or val.get("markdown", "")
                        if inner:
                            text = inner
                            break
            if not text:
                text = json.dumps(data, ensure_ascii=False)[:4000]
            return {"success": True, "content": text, "url": url, "provider": "cloudsway"}
        except Exception as e:
            return {"success": False, "content": str(e), "url": url, "provider": "cloudsway"}

    elif provider == "tavily":
        try:
            with httpx.Client(timeout=_TIMEOUT) as client:
                resp = client.post(
                    _TAVILY_EXTRACT_URL,
                    headers={"Authorization": f"Bearer {tv_key}", "Content-Type": "application/json"},
                    json={"urls": [url]},
                )
            if resp.status_code != 200:
                return {
                    "success": False,
                    "content": f"HTTP {resp.status_code}: {resp.text[:300]}",
                    "url": url,
                    "provider": "tavily",
                }
            data = resp.json()
            results = data.get("results", [])
            if results:
                text = results[0].get("raw_content") or results[0].get("text", "")
                return {"success": True, "content": text, "url": url, "provider": "tavily"}
            return {"success": False, "content": "No content returned", "url": url, "provider": "tavily"}
        except Exception as e:
            return {"success": False, "content": str(e), "url": url, "provider": "tavily"}

    else:
        return {
            "success": False,
            "content": "未配置联网工具 API Key（需设置 CLOUDSWAY_SEARCH_KEY 或 TAVILY_API_KEY）",
            "url": url,
            "provider": "none",
        }


def web_search(query: str, count: int = 10, user_id: Optional[str] = None) -> dict:
    cw_key, tv_key = _resolve_keys(user_id)
    provider = _provider(cw_key, tv_key)

    if provider == "cloudsway":
        try:
            with httpx.Client(timeout=_TIMEOUT) as client:
                resp = client.get(
                    _CW_SEARCH_URL,
                    headers={
                        "Authorization": f"Bearer {cw_key}",
                        "Pragma": "cache",
                    },
                    params={"q": query, "count": count},
                )
            if resp.status_code != 200:
                return {
                    "success": False,
                    "results": [],
                    "raw": f"HTTP {resp.status_code}: {resp.text[:300]}",
                    "provider": "cloudsway",
                }
            data = resp.json()
            results = []
            items = (
                data.get("results")
                or data.get("webPages", {}).get("value", [])
                or data.get("items", [])
                or (data if isinstance(data, list) else [])
            )
            for item in items[:count]:
                results.append({
                    "title": item.get("name") or item.get("title", ""),
                    "url": item.get("url") or item.get("link", ""),
                    "snippet": item.get("snippet") or item.get("description", ""),
                })
            return {"success": True, "results": results, "provider": "cloudsway"}
        except Exception as e:
            return {"success": False, "results": [], "raw": str(e), "provider": "cloudsway"}

    elif provider == "tavily":
        try:
            with httpx.Client(timeout=_TIMEOUT) as client:
                resp = client.post(
                    _TAVILY_SEARCH_URL,
                    headers={"Authorization": f"Bearer {tv_key}", "Content-Type": "application/json"},
                    json={"query": query, "max_results": count, "search_depth": "basic"},
                )
            if resp.status_code != 200:
                return {
                    "success": False,
                    "results": [],
                    "raw": f"HTTP {resp.status_code}: {resp.text[:300]}",
                    "provider": "tavily",
                }
            data = resp.json()
            results = []
            for item in data.get("results", [])[:count]:
                results.append({
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "snippet": item.get("content", ""),
                })
            return {"success": True, "results": results, "provider": "tavily"}
        except Exception as e:
            return {"success": False, "results": [], "raw": str(e), "provider": "tavily"}

    else:
        return {
            "success": False,
            "results": [],
            "raw": "未配置联网工具 API Key（需设置 CLOUDSWAY_SEARCH_KEY 或 TAVILY_API_KEY）",
            "provider": "none",
        }
