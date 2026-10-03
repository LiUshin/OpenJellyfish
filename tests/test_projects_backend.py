"""Project grouping, owner scoping, visible search and backup coverage."""

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI, Header, HTTPException

from app.deps import get_current_user
from app.routes.conversations import router as conversations_router
from app.routes.projects import router as projects_router
from app.services.backup import export_user_data
from app.services.conversations import (
    _write_meta,
    create_conversation,
    get_conversation,
    get_conversation_meta,
    save_message,
)
from app.services.projects import create_project, read_project_brief
from app.services.published import create_service, create_service_key, verify_service_key


class ProjectBackendTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.users = Path(self.tmp.name) / "users"
        self.users.mkdir()
        self.users_patch = patch("app.core.security.USERS_DIR", str(self.users))
        self.users_patch.start()
        self.app = FastAPI()
        self.app.include_router(projects_router)
        self.app.include_router(conversations_router)

        def actor(authorization: str = Header()):
            user_id = authorization.removeprefix("Bearer ")
            if user_id not in ("alice", "bob"):
                raise HTTPException(401)
            return {"user_id": user_id}

        self.app.dependency_overrides[get_current_user] = actor
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="http://test",
            headers={"Authorization": "Bearer alice"},
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        self.users_patch.stop()
        self.tmp.cleanup()

    async def test_project_crud_grouping_and_owner_scope(self):
        response = await self.client.post("/api/projects", json={"name": "  Launch  "})
        self.assertEqual(response.status_code, 200, response.text)
        project = response.json()
        self.assertEqual(project["name"], "Launch")
        pid = project["id"]
        self.assertEqual(project["brief_token_count"], 0)

        response = await self.client.put(f"/api/projects/{pid}/brief", json={"content": "# Goals\nShip launch"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["brief"], "# Goals\nShip launch")
        self.assertGreater(response.json()["brief_token_count"], 0)
        self.assertEqual(read_project_brief("alice", pid), "# Goals\nShip launch")

        with patch("app.runtime.chat.choice", return_value={"runtime": "deepagents", "model": "test"}):
            response = await self.client.post("/api/conversations", json={"title": "Planning", "project_id": pid})
        self.assertEqual(response.status_code, 200, response.text)
        conv = response.json()
        self.assertEqual(conv["project_id"], pid)
        self.assertEqual((await self.client.get(f"/api/projects/{pid}")).json()["conversation_count"], 1)
        self.assertEqual((await self.client.get("/api/projects")).json()[0]["conversation_count"], 1)

        # Runtime writes from a stale conversation snapshot retain a later move.
        stale = get_conversation("alice", conv["id"])
        response = await self.client.patch(f"/api/conversations/{conv['id']}/project", json={"project_id": None})
        self.assertEqual(response.status_code, 200, response.text)
        _write_meta("alice", conv["id"], stale)
        self.assertIsNone(get_conversation_meta("alice", conv["id"])["project_id"])

        response = await self.client.patch(f"/api/conversations/{conv['id']}/project", json={"project_id": pid})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["project_id"], pid)
        self.assertEqual((await self.client.get(f"/api/projects/{pid}", headers={"Authorization": "Bearer bob"})).status_code, 404)
        self.assertEqual((await self.client.patch(f"/api/conversations/{conv['id']}/project", json={"project_id": pid}, headers={"Authorization": "Bearer bob"})).status_code, 404)
        self.assertEqual((await self.client.get(f"/api/projects/{pid}/search?q=Launch", headers={"Authorization": "Bearer bob"})).status_code, 404)

        response = await self.client.delete(f"/api/projects/{pid}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(get_conversation_meta("alice", conv["id"])["project_id"])
        self.assertIsNotNone(get_conversation("alice", conv["id"]))

    async def test_service_key_cannot_access_project_api(self):
        # Use the real auth dependency: the other tests override it with an
        # Alice/Bob shortcut, which would not exercise the Service key boundary.
        self.app.dependency_overrides.pop(get_current_user)
        users_json = self.users / "users.json"
        users_json.write_text(json.dumps({
            "alice": {"username": "alice", "token": "admin-token"},
        }), encoding="utf-8")
        project = create_project("alice", "Admin only")
        service = create_service("alice", {"name": "Consumer", "published": True})
        key = create_service_key("alice", service["id"])["key"]
        self.assertEqual(verify_service_key(key)["service_id"], service["id"])

        with patch("app.core.security.USERS_JSON", str(users_json)):
            admin = await self.client.get("/api/projects", headers={"Authorization": "Bearer admin-token"})
            self.assertEqual(admin.status_code, 200, admin.text)
            service_headers = {"Authorization": f"Bearer {key}"}
            checks = (
                ("GET", "/api/projects", None),
                ("GET", f"/api/projects/{project['id']}", None),
                ("GET", f"/api/projects/{project['id']}/search?q=Admin", None),
                ("POST", "/api/projects", {"name": "Unauthorized"}),
                ("PUT", f"/api/projects/{project['id']}/brief", {"content": "unauthorized"}),
            )
            for method, url, body in checks:
                with self.subTest(method=method, url=url):
                    response = await self.client.request(method, url, headers=service_headers, json=body)
                    self.assertEqual(response.status_code, 401, response.text)
            self.assertEqual(read_project_brief("alice", project["id"]), "")

    async def test_project_search_visible_messages_only_and_cli_history(self):
        first = create_project("alice", "Search")
        second = create_project("alice", "Other")
        deep = create_conversation("alice", "Deep", first["id"])
        save_message("alice", deep["id"], "user", "Find the lunar sample")
        save_message("alice", deep["id"], "assistant", "The lunar sample is here",
                     tool_calls=[{"name": "internal", "args": {"secret": "needle-tool"}}])
        hidden = create_conversation("alice", "Other conversation", second["id"])
        save_message("alice", hidden["id"], "user", "lunar sample in another project")
        cli = create_conversation("alice", "CLI", first["id"])
        meta = get_conversation_meta("alice", cli["id"])
        _write_meta("alice", cli["id"], {**meta, "runtime_session_id": "fake-runtime"})

        with patch("app.runtime.chat.history", return_value=[
            {"role": "user", "content": "Please inspect lunar sample"},
            {"role": "assistant", "content": "Inspection complete"},
            {"role": "tool", "content": "needle-tool hidden"},
        ]):
            response = await self.client.get(f"/api/projects/{first['id']}/search?q=lunar")
            self.assertEqual(response.status_code, 200, response.text)
            data = response.json()
            self.assertEqual(data["total"], 2)
            self.assertEqual({hit["conversation_id"] for hit in data["results"]}, {deep["id"], cli["id"]})
            self.assertNotIn(hidden["id"], {hit["conversation_id"] for hit in data["results"]})
            tools = await self.client.get(f"/api/projects/{first['id']}/search?q=needle-tool")
            self.assertEqual(tools.json()["total"], 0)

    async def test_search_finds_assistant_text_blocks_but_not_process_blocks(self):
        project = create_project("alice", "Block search")
        conv = create_conversation("alice", "Blocks", project["id"])
        save_message("alice", conv["id"], "assistant", "Short final",
                     blocks=[
                         {"type": "thinking", "content": "private-thought-marker"},
                         {"type": "tool", "name": "read_file", "args": "private-tool-marker", "result": ""},
                         {"type": "text", "content": "Visible block marker"},
                     ])
        response = await self.client.get(f"/api/projects/{project['id']}/search?q=Visible%20block")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["total"], 1)
        self.assertIn("Visible block", response.json()["results"][0]["snippet"])
        for hidden in ("private-thought-marker", "private-tool-marker"):
            response = await self.client.get(f"/api/projects/{project['id']}/search", params={"q": hidden})
            self.assertEqual(response.json()["total"], 0)

    def test_project_files_are_in_conversation_backup_module(self):
        project = create_project("alice", "Backup")
        result = export_user_data("alice", ["conversations"], include_media=False)
        try:
            with zipfile.ZipFile(result.zip_path) as archive:
                self.assertIn(f"projects/{project['id']}/meta.json", archive.namelist())
                self.assertIn(f"projects/{project['id']}/brief.md", archive.namelist())
        finally:
            Path(result.zip_path).unlink()


if __name__ == "__main__":
    unittest.main()
