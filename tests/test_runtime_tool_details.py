import json
import unittest
from types import SimpleNamespace
from app.runtime.blocks import append_event
from app.runtime.tool_details import codex_tool, cursor_tool
from app.runtime.business_tools import BusinessTools


class ToolDetailsTests(unittest.TestCase):
    def test_codex_command_full_output_and_exit_status_survive_terminal(self):
        blocks = []
        append_event(blocks, 'tool', codex_tool({'id': 'cmd', 'type': 'commandExecution', 'command': 'python ' + 'x' * 9000}))
        append_event(blocks, 'tool', {'item_id': 'cmd', 'result_delta': 'partial'})
        self.assertEqual(blocks[0]['result'], 'partial')
        append_event(blocks, 'tool', codex_tool({'id': 'cmd', 'type': 'commandExecution', 'status': 'completed', 'aggregatedOutput': 'diagnostic output', 'exitCode': 2}, True))
        append_event(blocks, 'failed', {})
        restored = json.loads(json.dumps(blocks))[0]
        self.assertGreater(len(restored['args']), 8000)
        self.assertEqual(restored['result'], 'diagnostic output')
        self.assertEqual(restored['status'], 'failed')
        self.assertEqual(restored['exit_code'], 2)

    def test_codex_mcp_and_file_changes_keep_structured_details_only(self):
        payload = codex_tool({'id': 'mcp', 'type': 'mcpToolCall', 'tool': 'jellyfish_read_document',
            'arguments': {'path': '/docs/one.md'}, 'result': {'content': [{'type': 'text', 'text': 'DOCUMENT'}]}, 'auth': 'DO_NOT_PROJECT'}, True)
        blocks = []
        append_event(blocks, 'tool', payload)
        self.assertEqual(blocks[0]['name'], 'jellyfish_read_document')
        self.assertEqual(json.loads(blocks[0]['args']), {'path': '/docs/one.md'})
        self.assertIn('DOCUMENT', blocks[0]['result'])
        self.assertNotIn('DO_NOT_PROJECT', json.dumps(blocks))
        changes = [{'path': 'a.py', 'diff': '@@ -1 +1 @@\n-old\n+new'}]
        append_event(blocks, 'tool', codex_tool({'id': 'edit', 'type': 'fileChange', 'changes': changes}, True))
        self.assertEqual(blocks[1]['changes'], changes)

    def test_cursor_partial_updates_keep_input_result_and_diff(self):
        blocks = []
        for update in [
            {'toolCallId': 'a', 'kind': 'edit', 'title': 'Edit document', 'status': 'in_progress', 'rawInput': {'path': 'a.md', 'content': 'new'}},
            {'toolCallId': 'a', 'title': 'Done editing', 'content': [{'type': 'content', 'content': {'type': 'text', 'text': 'Detailed result'}}, {'type': 'diff', 'path': 'a.md', 'oldText': 'old', 'newText': 'new'}]},
            {'toolCallId': 'a', 'status': 'completed'},
        ]:
            append_event(blocks, 'tool', cursor_tool(update))
        self.assertEqual(len(blocks), 1)
        self.assertEqual(json.loads(blocks[0]['args'])['content'], 'new')
        self.assertEqual(blocks[0]['result'], 'Detailed result')
        self.assertEqual(blocks[0]['changes'][0]['new_text'], 'new')
        self.assertEqual(blocks[0]['status'], 'completed')

    def test_cursor_search_label_requires_web_tool_identity(self):
        cases = [
            ({'toolCallId': 'search_local_1', 'kind': 'search', 'title': 'Search files',
              'locations': [{'path': 'notes.md'}]}, 'search', '搜索'),
            ({'toolCallId': 'web_search_1', 'kind': 'search', 'title': 'Search files',
              'locations': []}, 'search', '搜索'),
            ({'toolCallId': 'web_search_2', 'kind': 'search', 'title': 'Web search: topic',
              'locations': [{'path': 'notes.md'}]}, 'search', '搜索'),
            ({'toolCallId': 'web_search_3', 'kind': 'search', 'title': 'Web search: topic',
              'locations': []}, 'webSearch', '网页搜索'),
        ]
        for update, kind, label in cases:
            with self.subTest(update=update):
                payload = cursor_tool(update)
                self.assertEqual(payload['kind'], kind)
                blocks = []
                append_event(blocks, 'tool', payload)
                self.assertEqual(blocks[0]['name'], label)
        blocks = []
        append_event(blocks, 'tool', {'item_id': 'search_unknown', 'kind': 'search'})
        self.assertEqual(blocks[0]['name'], '搜索')
        append_event(blocks, 'tool', codex_tool({'id': 'codex_web', 'type': 'webSearch'}))
        self.assertEqual(blocks[1]['name'], '网页搜索')

    def test_interrupted_tool_keeps_partial_output_with_unknown_status(self):
        blocks = []
        append_event(blocks, 'tool', {'item_id': 'a', 'kind': 'execute', 'result_delta': 'Partial output'})
        append_event(blocks, 'cancelled', {})
        self.assertEqual(blocks[0]['result'], 'Partial output')
        self.assertEqual(blocks[0]['status'], 'unknown')


class BusinessDetailsTests(unittest.IsolatedAsyncioTestCase):
    async def test_business_calls_store_complete_details_with_unique_call_ids(self):
        blocks = []
        bridge = BusinessTools(None, lambda *args: None, SimpleNamespace(emit=lambda run, kind, payload: append_event(blocks, kind, payload)))
        def invoke(actor_id, name, args, *, conversation_id=None):
            self.assertEqual(actor_id, 'alice')
            self.assertEqual(conversation_id, 'conversation-details')
            return {'documents': ['a.md', 'b.md']}
        bridge.invoke = invoke
        for _ in range(2):
            result = await bridge({'actor_id': 'alice', 'binding': {}, 'conversation_id': 'conversation-details'}, {}, {'tool': 'jellyfish_list_documents', 'arguments': {}})
            self.assertTrue(result['success'])
        self.assertEqual(len(blocks), 2)
        self.assertNotEqual(blocks[0]['event_key'], blocks[1]['event_key'])
        self.assertEqual(json.loads(blocks[0]['result'])['documents'], ['a.md', 'b.md'])
        self.assertEqual(json.loads(blocks[0]['args']), {'path': '/docs', 'offset': 0, 'limit': 20})



class ProviderDetailProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_codex_adapter_delivers_live_output_and_final_snapshot(self):
        import asyncio
        from pathlib import Path
        from app.runtime.codex import CodexAdapter
        class RPC:
            def __init__(self):
                self.events = asyncio.Queue()
                for method, params in [
                    ('item/started', {'item': {'id': 'cmd', 'type': 'commandExecution', 'command': 'python test.py'}}),
                    ('item/commandExecution/outputDelta', {'itemId': 'cmd', 'delta': 'live output'}),
                    ('item/completed', {'item': {'id': 'cmd', 'type': 'commandExecution', 'aggregatedOutput': 'live output\nfinished', 'exitCode': 0}}),
                    ('turn/completed', {'turn': {'status': 'completed'}}),
                ]:
                    self.events.put_nowait({'method': method, 'params': params})
            async def request(self, *args): return {'turn': {'id': 'turn'}}
            async def next_event(self): return await self.events.get()
        adapter = CodexAdapter('unused', Path('/tmp/unused-home'), Path('/tmp/unused-work'))
        adapter.rpc = RPC()
        events = [event async for event in adapter.stream_turn('thread', 'run script')]
        blocks = []
        for event in events:
            append_event(blocks, event.type, event.payload)
        self.assertEqual(blocks[0]['args'], 'python test.py')
        self.assertEqual(blocks[0]['result'], 'live output\nfinished')
        self.assertEqual(blocks[0]['status'], 'completed')

    async def test_cursor_adapter_delivers_raw_input_and_content_diff(self):
        import tempfile
        from pathlib import Path
        from app.runtime.cursor import CursorAdapter
        from test_runtime_cursor import FakeACP
        class RPC(FakeACP):
            async def request(self, method, params, **kwargs):
                if method == 'session/prompt':
                    for update in [
                        {'sessionUpdate': 'tool_call', 'toolCallId': 'edit', 'kind': 'edit', 'status': 'in_progress', 'rawInput': {'path': 'a.md', 'new_string': 'new'}},
                        {'sessionUpdate': 'tool_call_update', 'toolCallId': 'edit', 'status': 'completed', 'content': [{'type': 'diff', 'path': 'a.md', 'oldText': 'old', 'newText': 'new'}], 'rawOutput': {'replacements': 1}},
                    ]:
                        self.events.put_nowait({'method': 'session/update', 'params': {'sessionId': 'thread', 'update': update}})
                    return {'stopReason': 'end_turn'}
                return await super().request(method, params, **kwargs)
        with tempfile.TemporaryDirectory() as tmp:
            adapter = CursorAdapter('unused', Path(tmp), Path(tmp), 'model')
            adapter.rpc = RPC()
            events = [event async for event in adapter.stream_turn('thread', 'edit')]
        blocks = []
        for event in events:
            append_event(blocks, event.type, event.payload)
        self.assertEqual(json.loads(blocks[0]['args'])['new_string'], 'new')
        self.assertEqual(json.loads(blocks[0]['result'])['replacements'], 1)
        self.assertEqual(blocks[0]['changes'][0]['old_text'], 'old')
