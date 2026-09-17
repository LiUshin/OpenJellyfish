import asyncio
import os
import signal
import sys
import unittest
from unittest.mock import patch, AsyncMock

from app.runtime.rpc import StdioRPC, RuntimeFailure, RuntimeUnavailable


class ProcessCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_ps_is_rejected_before_spawning(self):
        import shutil
        which = shutil.which
        rpc = StdioRPC([sys.executable, '-c', 'pass'], env={'PATH': os.environ['PATH']}, cwd='/tmp')
        with patch('app.runtime.rpc.shutil.which', side_effect=lambda name, **kw: None if name == 'ps' else which(name, **kw)), \
             patch('app.runtime.rpc.asyncio.create_subprocess_exec', new_callable=AsyncMock) as spawn:
            with self.assertRaisesRegex(RuntimeUnavailable, 'procps'):
                await rpc.start()
            spawn.assert_not_awaited()
        self.assertIsNone(rpc.process)
        await rpc.close()

    async def test_detached_command_child_dies_with_transport(self):
        code = '''
import json, subprocess, sys
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'],
                         start_new_session=True, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
for line in sys.stdin:
    request = json.loads(line)
    print(json.dumps({'id': request['id'], 'result': {'pid': child.pid}}), flush=True)
'''
        rpc = StdioRPC([sys.executable, '-u', '-c', code], env={'PATH':os.environ['PATH']}, cwd='/tmp')
        child = None
        try:
            await rpc.start()
            child = (await rpc.request('child', {}))['pid']
            self.assertNotEqual(os.getpgid(child), rpc.process.pid)
            await rpc.capture_children()
            await rpc.close()
            self.assertEqual(await rpc.process_tree.live(), {})
        finally:
            if rpc.process and rpc.process.returncode is None:
                await rpc.close()
            if child:
                try:
                    os.kill(child, signal.SIGKILL)
                except ProcessLookupError:
                    pass


    async def test_close_is_idempotent_and_finishes_after_caller_cancellation(self):
        from unittest.mock import patch
        rpc = StdioRPC([sys.executable, '-c', 'import time; time.sleep(120)'], env={'PATH':os.environ['PATH']}, cwd='/tmp')
        entered, release = asyncio.Event(), asyncio.Event()
        await rpc.start()
        original = rpc.process_tree.terminate
        async def delayed_terminate():
            entered.set()
            await release.wait()
            await original()
        try:
            with patch.object(rpc.process_tree, 'terminate', delayed_terminate):
                caller = asyncio.create_task(rpc.close())
                await asyncio.wait_for(entered.wait(), 2)
                caller.cancel()
                await asyncio.sleep(.01)
                self.assertFalse(caller.done())
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await caller
            self.assertIsNotNone(rpc.process.returncode)
            await rpc.close()
        finally:
            release.set()
            await rpc.close()

    async def test_close_propagates_monitor_failure_while_joining(self):
        rpc = StdioRPC([], env={}, cwd='/tmp')
        release = asyncio.Event()
        async def monitor():
            await release.wait()
            rpc.monitor_error = RuntimeFailure('process inspection failed')
        rpc.monitor = asyncio.create_task(monitor())
        closing = asyncio.create_task(rpc.close())
        await asyncio.sleep(0)
        release.set()
        with self.assertRaises(RuntimeFailure):
            await closing

if __name__ == '__main__':
    unittest.main()
