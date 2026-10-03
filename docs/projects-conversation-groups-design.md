# Projects as conversation groups

## Purpose and boundary

An admin project groups conversations about one theme. **Projects exist only
on the admin side**; a Service and its consumer conversations have no project
field, project brief, project search, or project write access. A project does not create a new
workspace, document namespace, Agent core, or service. Existing documents remain
the source of durable working knowledge. The project brief is a small Markdown
context document for the group's conversations, not a replacement for `/docs`.
Service consumer conversations, scheduled tasks, and voice sessions are outside
this first version.

The Chat area keeps its existing primary navigation and file panel. Its secondary
column offers ungrouped conversations and projects. A project's main view shows
the brief and its conversations; opening one uses the existing chat view. A
conversation belongs to at most one project and can be moved without moving its
messages, attachments, or CLI runtime session.

## Durable data

- `users/{uid}/projects/{project_id}/meta.json`: ID, name, created and updated
  timestamps. IDs are opaque and scoped to the authenticated user.
- `users/{uid}/projects/{project_id}/brief.md`: the single Markdown brief for
  that project. The user and the admin Agent may edit the same document.
- `users/{uid}/conversations/{conversation_id}/meta.json`: nullable `project_id`.
  Older conversations have no project and remain usable.

Deleting a project leaves its conversations intact and ungrouped. Project and
conversation writes use the same per-conversation metadata update path so a
move cannot be lost when a chat turn updates its runtime metadata. Project files
are included with the conversations backup module.

## Brief context contract

The current project's brief is injected into **each** admin chat turn for all
three cores: DeepAgent, Codex, and Cursor CLI. It is loaded again for each turn,
so edits apply to existing conversations. It is clearly delimited as project
context, subordinate to system instructions, and never injected into ungrouped
or service consumer conversations. Changing a conversation's project changes
the brief used on its next turn.

The injected brief has a hard ceiling of **5,000 tokens including its wrapper**.
The server enforces the ceiling when reading context, regardless of how the
document was edited. When a brief exceeds the ceiling, the injected copy is
truncated at a Unicode boundary and marked as truncated; the saved Markdown is
not silently altered. A conservative UTF-8 byte bound is used across all
cores, so the rendered copy remains below the token ceiling even when a CLI
account uses a different model tokenizer. The UI shows this conservative
budget measure and warns when the injected copy would be shortened. This is a
budget for each injected brief copy, not the whole conversation context window.
Codex passes the copy as per-turn additional context. Cursor CLI currently has
no equivalent transient context channel, so its native conversation may retain
earlier per-turn copies until its own compaction; the 5,000-token ceiling still
applies to each new copy. Earlier copies are explicitly marked superseded.

Agent write access is one scoped brief write operation: its project is derived
from the current admin conversation, and it persists to `brief.md`. The Agent
can read the brief from its injected context or the same bridge. It cannot use
that operation to edit another project's brief. The UI presents one Markdown
write area; there is no need for a catalog of project-specific tools. CLI
workspace file writes alone are insufficient because that workspace is a
conversation copy and is not the project store.

Codex registers dynamic business tools when a native thread is created. Older
Codex threads created before this bridge cannot gain the new write tool without
losing native history; they still receive the brief, and a user can edit it in
the project view. New Codex conversations and DeepAgent/Cursor conversations
can use the scoped Agent write operation.

## Project search

Search is scoped to one authenticated user's project and includes titles plus
user-visible user and assistant text from its related conversations. It reads
DeepAgent JSONL messages and the CLI runtime history through the same
conversation abstraction used by chat. It excludes hidden reasoning, tool
payloads, binary attachments, and other projects. Results are bounded and
return the conversation, a short matched excerpt, and a location for opening
the conversation. The first version can scan on demand; an index can be added
later without changing the API or becoming authoritative data.

## API and migration

Project CRUD exposes project metadata and the brief. Conversation create and
move accept a validated nullable project ID. Existing conversation list/get
responses expose that ID. Project search has a dedicated project-scoped read
endpoint. All endpoints derive the user from authentication; callers cannot
choose an arbitrary user root. No bulk migration is needed: missing
`project_id` means ungrouped, and existing conversation layout remains intact.

## Acceptance checks

1. Create a project, edit its brief, create and move conversations, refresh the
   page, and see the same grouping and content.
2. Search a project and find matching DeepAgent and CLI user-visible turns,
   without results from a different project.
3. Confirm all three cores receive the current brief on each project turn; a
   later edit updates the next turn, and an oversized brief injects at most
   5,000 tokens with a truncation marker.
4. Confirm the admin Agent can persist a brief edit, while another project or
   a service consumer cannot be changed through that operation.
5. Delete a project and confirm its conversations still exist ungrouped, and
   backup/export includes the project data while present.
