"""Explicit real-account experiment; only boolean results are printed.

Uses only the dedicated auth file supplied by the operator. A single writer moves
its latest auth cache between private actor homes, saving Codex's refresh result
back after each process exits. No developer HOME/config/history is copied.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.runtime.codex import CodexAdapter


def write_private(path, data):
    tmp = path.with_suffix('.new')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def identity(data):
    auth = json.loads(data)
    tokens = auth.get('tokens') or {}
    if not tokens.get('account_id') or not tokens.get('access_token'):
        raise ValueError('Dedicated ChatGPT file login is required')
    return hashlib.sha256(tokens['account_id'].encode()).hexdigest()


async def verify(args):
    source = Path(args.auth).resolve()
    original_identity = identity(source.read_bytes())
    results = {'cloudflare': 'not_tested', 'independent_provider_accounts': 'not_tested',
               'cursor': 'not_tested', 'local_shared_identity': False}
    with tempfile.TemporaryDirectory(prefix='jellyfish-identity-') as folder:
        root = Path(folder)
        threads = {}

        async def turn(actor, message):
            home, workspace = root / actor / 'home', root / actor / 'workspace'
            home.mkdir(parents=True, exist_ok=True, mode=0o700)
            workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
            write_private(home / 'auth.json', source.read_bytes())
            (home / 'config.toml').write_text('cli_auth_credentials_store = "file"\n')
            adapter = CodexAdapter(args.codex, home, workspace, args.model)
            output, started = '', time.monotonic()
            try:
                thread = await adapter.open_session(str(workspace),
                    'This is a short state-isolation test. Do not call tools or access files.', threads.get(actor))
                threads[actor] = thread
                async with asyncio.timeout(180):
                    async for event in adapter.stream_turn(thread, message):
                        if event.type == 'text_delta':
                            output += event.payload['text']
                        elif event.type == 'request':
                            raise RuntimeError('Unexpected tool approval; test stopped')
                        elif event.type == 'cancelled':
                            raise RuntimeError('Unexpected cancellation')
            finally:
                await adapter.close()
                cache = home / 'auth.json'
                if cache.exists():
                    updated = cache.read_bytes()
                    if identity(updated) != original_identity:
                        raise RuntimeError('Identity changed during the experiment')
                    write_private(source, updated)
                    cache.unlink()
            return output, round(time.monotonic() - started, 2)

        marker_a, marker_b = uuid.uuid4().hex[:12], uuid.uuid4().hex[:12]
        _, t1 = await turn('actor-a', f'Remember my marker {marker_a}. Reply READY only.')
        _, t2 = await turn('actor-b', f'Remember my marker {marker_b}. Reply READY only.')
        # Re-create a home from saved state, with no running process or auth cache.
        shutil.copytree(root / 'actor-a', root / 'actor-a-restored')
        threads['actor-a-restored'] = threads['actor-a']
        answer, t3 = await turn('actor-a-restored', 'Return my remembered marker only.')
        results.update(local_shared_identity=marker_a in answer and marker_b not in answer,
                       separate_provider_threads=threads['actor-a'] != threads['actor-b'],
                       actor_state_restore=marker_a in answer,
                       auth_cache_removed=not list(root.rglob('auth.json')),
                       elapsed_seconds=[t1, t2, t3], model=args.model)
    Path(args.output).write_text(json.dumps(results, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(results, ensure_ascii=False))
    if not results['local_shared_identity']:
        raise SystemExit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--auth', required=True)
    parser.add_argument('--codex', required=True)
    parser.add_argument('--model', default=None)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        asyncio.run(verify(args))
    except Exception as exc:
        # Provider errors can contain account metadata. Do not print their payloads.
        print(f'Identity experiment failed: {type(exc).__name__}', file=sys.stderr)
        raise SystemExit(1)
