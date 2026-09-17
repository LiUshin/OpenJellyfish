#!/usr/bin/env python3
"""Explicit, opt-in real provider acceptance with synthetic Jellyfish actors.

Uses only the supplied dedicated Codex cache. Serialize against any other user of
that cache. No token/identity values are logged. Refresh is written back after
all child processes exit. Costs use the connected personal plan.
"""
import argparse
import asyncio
import json
import os
import secrets
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def main(args):
    root = Path(args.root).resolve(); root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.environ.update(JELLYFISH_RUNTIME_ENABLED='1', JELLYFISH_OWNER_USER_ID='bench_owner',
                      JELLYFISH_RUNTIME_DATA_DIR=str(root / 'runtime'), JELLYFISH_RUNTIME_CODEX_BIN=args.codex,
                      JELLYFISH_RUNTIME_RUN_TIMEOUT='300', DISABLE_SCHEDULER='1', DISABLE_WECHAT_CHANNEL='1',
                      RESTORE_VENVS_ON_STARTUP='0', STORAGE_BACKEND='local')
    from app.core.host_auth import HOST_ID
    from app.core import security
    from app.storage import local
    security.USERS_DIR = local.USERS_DIR = str(root / 'users')
    security.USERS_JSON = str(root / 'users' / 'users.json')
    users = {uid: {'username': name, 'token': secrets.token_hex(24), 'password_hash': security._hash_password('runtime-local-acceptance')}
             for uid, name in [('bench_owner','runtime_owner'),('bench_alice','runtime_alice'),('bench_bob','runtime_bob')]}
    security._save_users(users)
    from app.runtime.manager import get_runtime
    from app.runtime.vault import identity, private_write
    from app.runtime.store import TERMINAL
    from app.services.published import create_service
    from app.services.prompt import get_agent_notes
    from app.routes.runtime import router as runtime_router
    from app.routes.conversations import router as conversations_router
    from app.routes.chat import router as chat_router
    from fastapi import FastAPI
    import httpx
    app = FastAPI(); app.include_router(runtime_router); app.include_router(conversations_router); app.include_router(chat_router)
    manager = get_runtime()
    source = Path(args.auth); initial_identity = identity(source.read_bytes())
    p = manager.profiles.create(HOST_ID, 'Codex acceptance')
    manager.profiles.save_auth(p, source.read_bytes())
    pid = p['id']
    evidence = {'cloudflare':'not_tested', 'native_image':'disabled_unverified', 'independent_provider_accounts':'not_tested', 'runs':[]}
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://local')
    async def req(actor, method, path, body=None):
        response = await client.request(method, path, json=body, headers={'Authorization':'Bearer ' + users[actor]['token']})
        if response.status_code >= 400:
            raise RuntimeError(f'{method} {path}: HTTP {response.status_code}: {response.text[:400]}')
        return response.json()
    async def turn(actor, conv, message):
        run = await req(actor,'POST','/api/runtime/turns',{'conversation_id':conv['id'],'request_id':secrets.token_hex(16),'message':message})
        rid, seen, approved = run['id'], set(), 0
        while True:
            run = manager.store.get('run', rid)
            if run['status'] in TERMINAL: break
            pending = run.get('pending')
            if pending and pending['id'] not in seen:
                seen.add(pending['id'])
                command = pending.get('command') or ''
                # This fixed fixture authorizes only writes in its own scratch cwd.
                # Don't approve arbitrary commands inferred from model output.
                safe = 'runtime-proof.txt' in command and 'python' in command and all(bad not in command for bad in ['rm ', 'curl ', 'wget ', 'auth.json', 'http', '..', '/Users/', '/etc/', 'sh -c'])
                decision = 'accept' if safe and 'accept' in pending['allowed'] else 'decline'
                await req(actor,'POST',f'/api/runtime/runs/{rid}/approve',{'approval_id':pending['id'],'decision':decision})
                approved += decision == 'accept'
            await asyncio.sleep(.1)
        events = manager.store.events(rid, 0, 50000)
        info = {'actor':actor, 'status':run['status'],'approved':approved,
                'seconds':round(run.get('finished_at',time.time())-run['created_at'],2),
                'first_output_seconds':round(run['first_token_at']-run['created_at'],2) if run.get('first_token_at') else None,
                'business_tools':sorted({e['payload'].get('name') for e in events if e['type']=='business_tool' and e['payload'].get('status')=='completed'}),
                'artifacts':[a['name'] for a in run.get('artifacts',[])],
                'error':run.get('error')}
        evidence['runs'].append(info)
        print(json.dumps(info,ensure_ascii=False),flush=True)
        if run['status'] != 'completed': raise RuntimeError('Real run did not complete')
        return run
    try:
        await manager.profiles.probe(HOST_ID,pid)
        models = manager.profiles.get(pid)['models']
        if args.model not in [m['id'] for m in models]: raise RuntimeError('Requested model not available')
        for actor in ('bench_alice','bench_bob'):
            manager.profiles.grant(HOST_ID,pid,actor,[args.model])
        manager.profiles.grant(HOST_ID,pid,'bench_owner',[args.model])
        marker = 'RUNTIME-' + secrets.token_hex(5)
        manager.runs.storage.write_text('bench_owner','/docs/acceptance.txt',marker)
        service = create_service('bench_owner', {'name':'Runtime acceptance service','allowed_docs':['acceptance.txt']})
        choice = {'runtime':'codex','profile_id':pid,'model':args.model,'image_mode':'off'}
        owner = await req('bench_owner','POST','/api/conversations',{'title':'Runtime owner acceptance','runtime_choice':choice,'context_paths':['/docs/acceptance.txt']})
        prompt = (f'这是受控验收。依次实际调用 jellyfish_read_document 读取 /docs/acceptance.txt；'
                  f'调用 jellyfish_read_service_document 读取 service_id={service["id"]} 的同一文档；'
                  '调用 jellyfish_read_memory，然后 jellyfish_update_memory 保存记忆“本用户喜欢简洁中文”，使用刚读到的 sha256。'
                  '最后调用命令执行工具运行 python3 -c "from pathlib import Path; Path(\'runtime-proof.txt\').write_text(\'runtime acceptance passed\')"。'
                  '命令如需审批请请求，不要改用其他方式。回复文档中的标记。不要生图，不要访问工作目录外的文件。')
        first = await turn('bench_owner',owner,prompt)
        evidence['owner_document_marker'] = marker in first['output']
        evidence['memory_written'] = '简洁中文' in get_agent_notes('bench_owner')
        evidence['owner_session_id'] = owner['runtime_session_id']
        # Two trusted app admins use the same profile but distinct provider threads.
        convs = []
        for actor in ('bench_alice','bench_bob'):
            conv = await req(actor,'POST','/api/conversations',{'title':actor + ' acceptance','runtime_choice':choice})
            convs.append((actor,conv))
            await turn(actor,conv,'请记住本会话的标记 ' + actor.upper() + '。只回复此标记。')
        resumed = await turn('bench_alice',convs[0][1],'请只回复本会话上一轮我要求记住的标记。')
        evidence['admin_multiturn'] = 'BENCH_ALICE' in resumed['output'] and 'BENCH_BOB' not in resumed['output']
        sessions = [manager.store.get('session',c['runtime_session_id']) for _,c in convs]
        evidence['separate_provider_threads'] = sessions[0]['thread_id'] != sessions[1]['thread_id']
        denied = await client.get('/api/runtime/sessions/' + convs[0][1]['runtime_session_id'],headers={'Authorization':'Bearer '+users['bench_bob']['token']})
        evidence['cross_actor_http_denied'] = denied.status_code == 404
        evidence['actor_auth_cache_removed'] = not list((root/'runtime'/'actors').rglob('auth.json'))
        evidence['profile_id'] = pid
        evidence['passed'] = all(evidence[k] for k in ['owner_document_marker','memory_written','admin_multiturn','separate_provider_threads','cross_actor_http_denied','actor_auth_cache_removed']) and any(r['approved'] for r in evidence['runs']) and bool(first['artifacts'])
        if not evidence['passed']: raise RuntimeError('One or more acceptance assertions failed')
    finally:
        await manager.shutdown(); await client.aclose()
        # Read vault directly after shutdown; same identity required at both ends.
        updated = manager.profiles.vault.read(pid)
        if identity(updated) != initial_identity or identity(source.read_bytes()) != initial_identity:
            raise RuntimeError('Identity changed; refused to overwrite the dedicated cache')
        private_write(source, updated)
        Path(args.output).write_text(json.dumps(evidence,ensure_ascii=False,indent=2))
        print(json.dumps({'evidence':args.output,'passed':evidence.get('passed',False)}),flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    for key in ('auth','codex','root','output'): parser.add_argument('--'+key,required=True)
    parser.add_argument('--model',default='gpt-5.6-sol')
    asyncio.run(main(parser.parse_args()))
