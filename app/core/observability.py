import os
from typing import Optional, Dict, Any, List

_langfuse_handler = None


def is_langfuse_enabled() -> bool:
    enabled = os.getenv("LANGFUSE_ENABLED", "false").lower() == "true"
    has_keys = bool(os.getenv("LANGFUSE_PUBLIC_KEY")) and bool(os.getenv("LANGFUSE_SECRET_KEY"))
    return enabled and has_keys


def _ensure_env_vars():
    host = os.getenv("LANGFUSE_HOST")
    if host and not os.getenv("LANGFUSE_BASE_URL"):
        os.environ["LANGFUSE_BASE_URL"] = host


def get_langfuse_handler():
    global _langfuse_handler
    if not is_langfuse_enabled():
        return None
    if _langfuse_handler is None:
        _ensure_env_vars()
        try:
            try:
                from langfuse.callback import CallbackHandler
            except ImportError:
                from langfuse.langchain import CallbackHandler
            _langfuse_handler = CallbackHandler()
            print(f"[Langfuse] Initialized, host={os.getenv('LANGFUSE_HOST', 'N/A')}")
        except ImportError as e:
            print(f"[Langfuse] Not installed: {e}")
            return None
        except Exception as e:
            print(f"[Langfuse] Init failed: {e}")
            return None
    return _langfuse_handler


def get_langfuse_config(
    thread_id: Optional[str] = None,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    tags: Optional[List[str]] = None,
    extra_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    config: Dict[str, Any] = {"configurable": {}}
    if thread_id:
        config["configurable"]["thread_id"] = thread_id
    handler = get_langfuse_handler()
    if handler:
        config["callbacks"] = [handler]
        metadata: Dict[str, Any] = {}
        if session_id or thread_id:
            metadata["langfuse_session_id"] = session_id or thread_id
        if user_id:
            metadata["langfuse_user_id"] = user_id
        if tags:
            metadata["langfuse_tags"] = tags
        if extra_metadata:
            metadata.update(extra_metadata)
        if metadata:
            config["metadata"] = metadata
    return config


def get_langfuse_callbacks() -> List:
    handler = get_langfuse_handler()
    return [handler] if handler else []


def get_langfuse_metadata(
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    tags: Optional[List[str]] = None,
) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {}
    if session_id:
        metadata["langfuse_session_id"] = session_id
    if user_id:
        metadata["langfuse_user_id"] = user_id
    if tags:
        metadata["langfuse_tags"] = tags
    return metadata


def flush_langfuse():
    handler = _langfuse_handler
    if handler:
        try:
            handler.flush()
        except Exception:
            pass


def shutdown_langfuse():
    global _langfuse_handler
    if _langfuse_handler:
        try:
            _langfuse_handler.flush()
            print("[Langfuse] Shutdown")
        except Exception:
            pass
        finally:
            _langfuse_handler = None
