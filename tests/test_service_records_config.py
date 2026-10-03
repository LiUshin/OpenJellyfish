"""The new area grant must not be silently inherited or recreated via the legacy flag."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from app.services.memory_tools import get_soul_config, save_soul_config


class ServiceRecordsConfigTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_grant_is_visible_but_cannot_be_newly_issued(self):
        from app.routes.settings_routes import api_update_soul_config

        with tempfile.TemporaryDirectory() as temp:
            with patch('app.core.security.USERS_DIR', temp), \
                 patch('app.routes.settings_routes.sync_soul_symlink'), \
                 patch('app.services.agent.clear_agent_cache'):
                user = {'user_id': 'alice'}
                save_soul_config('alice', {'include_consumer_conversations': True})
                current = get_soul_config('alice')
                self.assertTrue(current['include_consumer_conversations'])
                self.assertFalse(current['service_records_enabled'])

                with self.assertRaises(HTTPException) as error:
                    await api_update_soul_config({'include_consumer_conversations': True}, user)
                self.assertEqual(error.exception.status_code, 422)

                enabled = await api_update_soul_config({'service_records_enabled': True}, user)
                self.assertTrue(enabled['config']['service_records_enabled'])
                self.assertFalse(enabled['config']['include_consumer_conversations'])

                disabled = await api_update_soul_config({'service_records_enabled': False}, user)
                self.assertFalse(disabled['config']['service_records_enabled'])
                self.assertFalse(disabled['config']['include_consumer_conversations'])
                self.assertTrue((Path(temp) / 'alice/soul/config.json').exists())


if __name__ == '__main__':
    unittest.main()
