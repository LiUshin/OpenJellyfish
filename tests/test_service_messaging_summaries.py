"""Paged Agent summaries must stay owner-scoped and omit delivery targets."""

import json
import tempfile
import unittest
from pathlib import Path

from app.services.service_messaging import MessageStore


class CaseSummaryTests(unittest.TestCase):
    def test_owner_paging_and_projection(self):
        with tempfile.TemporaryDirectory() as temp:
            store = MessageStore(Path(temp) / 'messages.sqlite3')
            try:
                for owner, cid, timestamp in (
                    ('alice', 'a1', '2026-10-01T10:00:00Z'),
                    ('alice', 'a2', '2026-10-02T10:00:00Z'),
                    ('bob', 'b1', '2026-10-03T10:00:00Z'),
                ):
                    record = {'id': cid, 'service_id': 'svc_one', 'message': cid,
                              'timestamp': timestamp, 'target': {'token': 'secret'}}
                    store.db.execute('INSERT INTO sm_cases(id,owner,service,conversation,record) VALUES(?,?,?,?,?)',
                                     (cid, owner, 'svc_one', 'conv_one', json.dumps(record)))
                first = store.list_case_summaries('alice', 0, 1)
                second = store.list_case_summaries('alice', 1, 1)
                self.assertEqual([row['id'] for row in first + second], ['a2', 'a1'])
                self.assertNotIn('target', first[0])
                self.assertEqual(store.list_case_summaries('alice', 2, 1), [])
            finally:
                store.close()


if __name__ == '__main__':
    unittest.main()
