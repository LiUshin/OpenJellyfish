"""POSIX process tracking for trusted local commands that create new sessions.

This is lifecycle cleanup, not a containment boundary for hostile processes.
Only process IDs, parent/group IDs, state and start time are inspected.
"""
import asyncio
import contextlib
import os
import signal

from app.runtime.rpc_errors import RuntimeFailure


async def snapshot():
    process = await asyncio.create_subprocess_exec(
        'ps', '-axo', 'pid=,ppid=,pgid=,stat=,lstart=',
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), 3)
    except BaseException:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        await process.wait()
        raise
    if process.returncode:
        raise RuntimeFailure('无法检查本机子进程；连接已暂停，请检查宿主权限')
    result = {}
    for line in output.decode().splitlines():
        fields = line.split(None, 4)
        if len(fields) == 5:
            pid, parent, group, state, started = fields
            result[int(pid)] = (int(parent), int(group), state, started)
    return result


class ProcessTree:
    def __init__(self, root):
        self.root = root
        self.seen = {}

    async def capture(self):
        rows = await snapshot()
        family = {self.root}
        while True:
            children = {pid for pid, row in rows.items() if row[0] in family}
            expanded = family | children
            if expanded == family:
                break
            family = expanded
        for pid in family - {self.root}:
            if pid in rows:
                self.seen[pid] = rows[pid][3]

    async def live(self):
        rows = await snapshot()
        return {pid: rows[pid] for pid, started in self.seen.items()
                if pid in rows and rows[pid][3] == started and not rows[pid][2].startswith('Z')}

    async def terminate(self):
        for sig in (signal.SIGTERM, signal.SIGKILL):
            current = await self.live()
            for pid in current:
                try:
                    os.kill(pid, sig)
                except ProcessLookupError:
                    pass
            for _ in range(10):
                if not await self.live():
                    return
                await asyncio.sleep(.05)
        if await self.live():
            raise RuntimeFailure('执行子进程尚未退出；连接已暂停，需检查宿主进程')
