"""Project provider tool records explicitly; never persist the RPC/auth envelope."""
import json


def display(value):
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)


def codex_tool(item, completed=False):
    payload = {"item_id": item.get("id"), "kind": item.get("type"),
               "status": item.get("status") or ("completed" if completed else "inProgress")}
    if item.get("tool"):
        payload["name"] = item["tool"]
    for source, target in (("arguments", "input"), ("aggregatedOutput", "result"),
                           ("result", "result"), ("changes", "changes"),
                           ("command", "command"), ("query", "command"),
                           ("action", "input"), ("results", "result")):
        if item.get(source) is not None:
            payload[target] = item[source]
    if item.get("error"):
        payload.update(status="failed", result=item["error"])
    if item.get("exitCode") not in (None, 0):
        payload["status"] = "failed"
    if "exitCode" in item:
        payload["exit_code"] = item["exitCode"]
    return payload


def cursor_tool(update):
    payload = {"item_id": update.get("toolCallId"), "kind": update.get("kind"),
               "status": update.get("status"), "command": update.get("title")}
    if update.get("rawInput") is not None:
        payload["input"] = update["rawInput"]
    if update.get("rawOutput") is not None:
        payload["result"] = update["rawOutput"]
    texts, changes = [], []
    for entry in update.get("content") or []:
        if entry.get("type") == "content":
            content = entry.get("content", {})
            if content.get("type") == "text":
                texts.append(content.get("text", ""))
        elif entry.get("type") == "diff" and entry.get("path"):
            changes.append({"path": entry["path"], "old_text": entry.get("oldText") or "",
                            "new_text": entry.get("newText") or ""})
    if texts and "result" not in payload:
        payload["result"] = "\n".join(texts)
    if changes:
        payload["changes"] = changes
    return payload
