#!/usr/bin/env python3
"""Host-only runtime credential import/recovery. Stop the API process first."""
import argparse
import asyncio
import os
import shutil
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def main(args):
    from app.runtime.manager import get_runtime
    from app.core.host_auth import HOST_ID
    manager = get_runtime()
    try:
        owner = HOST_ID
        manager.profiles.owner(owner)
        if args.command == 'import-codex':
            if args.default and (not args.model or not args.admin_user_id):
                raise ValueError('--default requires --model and --admin-user-id')
            source = Path(args.auth_file).expanduser().resolve()
            if not source.is_file() or source.stat().st_size > 128 * 1024:
                raise ValueError('Dedicated auth file missing or too large')
            p = manager.profiles.create(owner, args.name)
            manager.profiles.save_auth(p, source.read_bytes())
            await manager.profiles.probe(owner, p['id'])
            if args.default:
                if not args.model:
                    raise ValueError('--default requires an explicit --model')
                selected = {'runtime':'codex','profile_id':p['id'],'model':args.model,'image_mode':'native'}
                manager.profiles.binding(owner,p['id'],args.model)
                manager.profiles.grant(owner,p['id'],args.admin_user_id,[args.model])
                manager.store.put('preference',{'id':args.admin_user_id,'choice':selected})
            print('Codex connection ready; profile_id=' + p['id'])
            print('Stop using the source auth cache in any other process; this runtime now owns its refresh lifecycle.')
        elif args.command == 'recover':
            p = manager.profiles.managed(owner, args.profile_id)
            if not args.confirm_stopped:
                raise ValueError('Inspect and stop all old Codex processes for this profile, then use --confirm-stopped')
            recorded_pids = set(p.get('recovery_pids', []))
            for r in manager.store.all('run'):
                if r['binding']['profile_id'] == p['id'] and r.get('process_pid') and r.get('status') == 'failed':
                    recorded_pids.add(r['process_pid'])
            logins = manager.store.find('login', profile_id=p['id'])
            for attempt in logins:
                if attempt.get('cleanup_required') and attempt.get('process_pid'):
                    recorded_pids.add(attempt['process_pid'])
            for pid in recorded_pids:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    pass
                else:
                    raise ValueError('A recorded process PID still exists; inspect the host before recovery')
            if p.get('active_lease'):
                home = (manager.store.root / p['active_lease']['home']).resolve()
                home.relative_to(manager.store.root.resolve())
                manager.profiles.provider(p).credential_path(home).unlink(missing_ok=True)
            for attempt in logins:
                if attempt.get('cleanup_required'):
                    shutil.rmtree(manager.store.root / 'login' / attempt['id'], ignore_errors=True)
                    attempt['cleanup_required'] = False
                    manager.store.put('login', attempt)
            for s in manager.store.all('session'):
                if s['binding']['profile_id'] == p['id']:
                    manager.profiles.provider(p).credential_path(manager.backend.session_dir(s)/'home').unlink(missing_ok=True)
            p.pop('active_lease', None)
            p.pop('recovery_pids', None)
            p.update(recovery_required=False,status='disconnected',auth_generation=p['auth_generation']+1,account=None,models=[])
            manager.profiles.vault.delete(p['id'])
            manager.store.put('profile',p)
            print('Fence cleared. Fresh login and grants are required; old runs were not replayed.')
    finally:
        await manager.shutdown()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command',required=True)
    imp = sub.add_parser('import-codex')
    imp.add_argument('--auth-file',required=True,help='Dedicated personal ChatGPT auth.json, never a shared live HOME')
    imp.add_argument('--name',default='我的 Codex')
    imp.add_argument('--model')
    imp.add_argument('--admin-user-id',help='Admin receiving an explicit grant and default model')
    imp.add_argument('--default',action='store_true')
    recovery = sub.add_parser('recover')
    recovery.add_argument('--profile-id',required=True)
    recovery.add_argument('--confirm-stopped',action='store_true')
    try:
        asyncio.run(main(parser.parse_args()))
    except Exception as exc:
        # Keep credential parser/provider error payloads out of the terminal.
        print('Runtime operation failed: verify owner configuration, stop the API worker, and check the dedicated login/profile.',file=sys.stderr)
        sys.exit(1)
