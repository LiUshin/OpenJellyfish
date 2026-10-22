"""Context snapshots must not be confused with cumulative token billing."""

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.runtime.codex import CodexAdapter
from app.runtime.context_usage import codex_context_usage
from app.runtime.types import RuntimeEvent
from app.routes.runtime import session as session_snapshot
import test_runtime_service as service_tests


class ContextNormalizationTests(unittest.TestCase):
    def test_uses_latest_response_and_never_thread_total(self):
        self.assertEqual(codex_context_usage({
            'last': {'totalTokens': 604100, 'inputTokens': 600000},
            'total': {'totalTokens': 9000000}, 'modelContextWindow': 1000000,
            'private': {'account': 'not-for-ui'},
        }), {'used_tokens': 604100, 'limit_tokens': 1000000, 'source': 'codex'})

    def test_zero_is_reported_and_optional_fields_stay_unknown(self):
        self.assertEqual(codex_context_usage({'last': {'totalTokens': 0}}),
                         {'used_tokens': 0, 'limit_tokens': None, 'source': 'codex'})
        self.assertEqual(codex_context_usage({'modelContextWindow': 0}),
                         {'used_tokens': None, 'limit_tokens': 0, 'source': 'codex'})
        self.assertEqual(codex_context_usage({'total': {'totalTokens': 6000},
                                             'modelContextWindow': 128000}),
                         {'used_tokens': None, 'limit_tokens': 128000, 'source': 'codex'})

    def test_invalid_counts_are_not_coerced_or_reported_as_zero(self):
        for value in (True, False, -1, 2**53, '12000', '', 1.5, 0.0,
                      float('nan'), float('inf'), float('-inf'), [], {}, None):
            with self.subTest(value=repr(value)):
                self.assertEqual(codex_context_usage({
                    'last': {'totalTokens': value}, 'modelContextWindow': value,
                }), {'used_tokens': None, 'limit_tokens': None, 'source': 'codex'})

    def test_malformed_or_absent_snapshots_remain_unknown(self):
        for snapshot in (None, [], False, 'raw', {}, {'last': []}, {'last': None}):
            with self.subTest(snapshot=snapshot):
                self.assertEqual(codex_context_usage(snapshot),
                                 {'used_tokens': None, 'limit_tokens': None, 'source': 'codex'})


class ContextAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_context_event_is_independent_of_billing_and_tracks_compaction(self):
        class RPC:
            async def request(self, method, params):
                snapshots = [
                    {'total': {'inputTokens': 9000000, 'outputTokens': 30},
                     'last': {'inputTokens': 5980, 'outputTokens': 20, 'totalTokens': 6000},
                     'modelContextWindow': 10000},
                    # Same billing counters, lower latest response after compaction.
                    {'total': {'inputTokens': 9000000, 'outputTokens': 30},
                     'last': {'inputTokens': 980, 'outputTokens': 20, 'totalTokens': 1000},
                     'modelContextWindow': 10000},
                    {'last': {'totalTokens': 0}, 'modelContextWindow': None},
                    [],
                ]
                self.events = [{'method': 'thread/tokenUsage/updated', 'params': {
                    'threadId': 'thread', 'turnId': 'turn', 'tokenUsage': snapshot,
                }} for snapshot in snapshots] + [{'method': 'turn/completed', 'params': {
                    'threadId': 'thread', 'turnId': 'turn', 'turn': {'status': 'completed'},
                }}]
                return {'turn': {'id': 'turn'}}

            async def next_event(self):
                return self.events.pop(0)

        adapter = CodexAdapter('unused', Path('/tmp/home'), Path('/tmp/work'), 'model')
        adapter.rpc = RPC()
        events = [event async for event in adapter.stream_turn('thread', 'hello')]
        self.assertEqual([event.payload['context_usage'] for event in events
                          if event.type == 'context_usage'], [
            {'used_tokens': 6000, 'limit_tokens': 10000, 'source': 'codex'},
            {'used_tokens': 1000, 'limit_tokens': 10000, 'source': 'codex'},
            {'used_tokens': 0, 'limit_tokens': None, 'source': 'codex'},
            {'used_tokens': None, 'limit_tokens': None, 'source': 'codex'},
        ])
        self.assertEqual([event.payload['usage'] for event in events if event.type == 'usage'],
                         [{'inputTokens': 5980, 'outputTokens': 20, 'totalTokens': 6000}])


class ContextPersistenceTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = service_tests.ServiceTests.asyncSetUp
    asyncTearDown = service_tests.ServiceTests.asyncTearDown
    session = service_tests.ServiceTests.session
    until = service_tests.ServiceTests.until

    async def test_context_persists_in_owned_session_and_event_replay_without_billing_changes(self):
        usage = {'inputTokens': 20000, 'outputTokens': 1000, 'totalTokens': 21000}
        context = {'used_tokens': 6000, 'limit_tokens': 10000, 'source': 'codex'}

        async def stream_turn(adapter, thread_id, text):
            yield RuntimeEvent('usage', {'usage': usage})
            yield RuntimeEvent('context_usage', {'context_usage': context})
            yield RuntimeEvent('completed', {})

        session = self.session()
        with patch.object(service_tests.FakeAdapter, 'stream_turn', stream_turn):
            run = self.service.enqueue('a', session['id'], 'context-request', 'hello')
            await self.until(lambda: self.store.get('run', run['id'])['status'] == 'completed')
        stored = self.store.get('run', run['id'])
        self.assertEqual(stored['usage'], usage)
        self.assertEqual(stored['context_usage'], context)
        events = self.store.events(run['id'])
        self.assertEqual([event['payload']['context_usage'] for event in events
                          if event['type'] == 'context_usage'], [context])
        snapshot = await session_snapshot(session['id'], user={'user_id': 'a'},
            runtime=SimpleNamespace(runs=self.service, store=self.store))
        self.assertEqual(snapshot['runs'][0]['context_usage'], context)
        self.assertEqual(snapshot['runs'][0]['usage'], usage)


if __name__ == '__main__':
    unittest.main()
