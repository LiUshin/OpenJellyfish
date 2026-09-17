"""Owner-managed supplier OAuth lifecycle and serialized credential refresh."""
import asyncio
import contextlib
import shutil
import time
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import HTTPException
from app.core.roles import active_user, is_owner
from app.core.host_auth import HOST_ID
from app.runtime.codex import CodexAdapter
from app.runtime.rpc import RuntimeFailure
from app.runtime.service import new_id
from app.runtime.vault import CredentialVault, private_write
from app.runtime.providers import CodexProvider


class ProfileManager:
    def __init__(self, store, policy, executable, *, adapter_factory=CodexAdapter, providers=None):
        self.store, self.policy, self.executable = store, policy, executable
        self.adapter_factory = adapter_factory
        self.providers = providers or {'codex': CodexProvider(executable, adapter_factory)}
        self.vault = CredentialVault(store.root)
        self.locks, self.logins = {}, {}
        self.migrate_host_ownership()
        self.runs = None
        for p in self.store.all('profile'):
            if p.get('login_id') and not any(a['id'] == p['login_id'] and a['status'] == 'pending' for a in self.store.all('login')):
                p.update(login_id=None, status='disconnected')
                self.store.put('profile', p)

    def migrate_host_ownership(self):
        """Atomically move legacy connections; preserve explicit revocations/history."""
        with self.store.db:
            for p in self.store.all('profile'):
                previous = p.get('credential_owner_id')
                if previous == HOST_ID:
                    continue
                p.update(credential_owner_id=HOST_ID, migrated_from_admin=previous)
                self.store._put('profile', p)
                prior = self.store.find('grant', profile_id=p['id'], actor_id=previous)
                g = prior[0] if prior else None
                if not g and previous and active_user(previous):
                    g = {'id': new_id(), 'profile_id': p['id'], 'actor_id': previous,
                         'version': 1, 'enabled': True, 'models': [m['id'] for m in p.get('models', [])],
                         'auth_generation': p['auth_generation'], 'updated_at': time.time(),
                         'granted_by': HOST_ID, 'reason': 'legacy_owner_migration'}
                    self.store._put('grant', g)
                for session in self.store.all('session'):
                    binding = session['binding']
                    if binding.get('profile_id') != p['id']:
                        continue
                    binding['credential_owner_id'] = HOST_ID
                    if session['actor_id'] == previous and not binding.get('grant_id') and g:
                        binding.update(grant_id=g['id'], grant_version=g['version'])
                    self.store._put('session', session)

    def owner(self, actor_id):
        self.policy.require_connection_owner(actor_id)
        if not is_owner(actor_id) and not active_user(actor_id):
            raise HTTPException(403, '用户不可用')

    def get(self, pid):
        p = self.store.get('profile', pid)
        if not p:
            raise HTTPException(404, '连接不存在')
        return p

    def managed(self, actor_id, pid):
        self.owner(actor_id)
        p = self.get(pid)
        if p['credential_owner_id'] != actor_id:
            raise HTTPException(404, '连接不存在')
        return p

    def provider(self, profile):
        provider = self.providers.get(profile.get('runtime', 'codex'))
        if not provider:
            raise HTTPException(409, '连接引擎未接入')
        return provider

    def create(self, actor_id, name, runtime='codex'):
        self.owner(actor_id)
        self.provider({'runtime': runtime})
        if len(self.store.find('profile', credential_owner_id=actor_id)) >= 8:
            raise HTTPException(409, '最多保存 8 个连接')
        p = {'id': new_id(), 'name': name, 'runtime': runtime, 'credential_owner_id': actor_id,
             'auth_generation': 1, 'identity': None, 'status': 'disconnected', 'models': [],
             'created_at': time.time(), 'login_id': None, 'account': None, 'recovery_required': False}
        return self.store.put('profile', p)

    def public(self, p, actor_id):
        result = {k: p.get(k) for k in ('id', 'name', 'runtime', 'status', 'models', 'auth_generation', 'recovery_required')}
        result.update(source='personal' if p['credential_owner_id'] == actor_id else 'owner_shared',
                      can_manage=is_owner(actor_id) and p['credential_owner_id'] == actor_id,
                      image_modes=['native'], capabilities=p.get('capabilities', {}))
        if self.runs and hasattr(getattr(self.runs, 'backend', None), 'public_state'):
            result['worker'] = self.runs.backend.public_state(p['id'])
        if result['can_manage']:
            result['account'] = p.get('account')
            result['login_id'] = p.get('login_id')
        return result

    def list(self, actor_id):
        result = []
        for p in self.store.all('profile'):
            if p['credential_owner_id'] == actor_id and is_owner(actor_id):
                result.append(self.public(p, actor_id))
                continue
            grants = self.store.find('grant', profile_id=p['id'], actor_id=actor_id, enabled=True)
            if not grants or not is_owner(p['credential_owner_id']):
                continue
            g = grants[0]
            item = self.public(p, actor_id)
            item['models'] = [m for m in p['models'] if m['id'] in g['models']]
            if g['auth_generation'] != p['auth_generation']:
                item['status'] = 'authorization_required'
            result.append(item)
        return result

    def authorize(self, actor_id, binding):
        self.policy.ensure_supported()
        if not is_owner(actor_id) and not active_user(actor_id):
            raise HTTPException(403, '用户不可用')
        p = self.get(binding['profile_id'])
        if binding.get('runtime', p['runtime']) != p['runtime']:
            raise HTTPException(409, '会话引擎与连接不一致')
        self.provider(p)
        if p.get('recovery_required'):
            raise HTTPException(503, '连接需要宿主检查旧进程后恢复')
        if p['status'] != 'ready' or not is_owner(p['credential_owner_id']):
            raise HTTPException(409, '连接未就绪，请联系超管')
        if p['auth_generation'] != binding['auth_generation']:
            raise HTTPException(409, '连接账号已改变，请重新选择连接并创建会话')
        if actor_id != p['credential_owner_id'] or not is_owner(actor_id):
            g = self.store.get('grant', binding.get('grant_id', ''))
            if not g or g['profile_id'] != p['id'] or g['actor_id'] != actor_id or not g['enabled'] or g['version'] != binding.get('grant_version') or g['auth_generation'] != p['auth_generation']:
                raise HTTPException(403, '此连接授权已失效，请联系超管后创建新会话')
            if binding.get('model') not in g['models']:
                raise HTTPException(403, '此模型未获授权')
        if binding.get('model') not in {m['id'] for m in p['models']}:
            raise HTTPException(409, '此模型已不可用，请选择其他模型')
        if binding.get('image_mode', 'native') not in ('off', 'native'):
            raise HTTPException(400, '不支持此生图模式')
        return p

    def binding(self, actor_id, pid, model, image_mode='native'):
        p = self.get(pid)
        binding = {'runtime': p['runtime'], 'profile_id': pid, 'credential_owner_id': p['credential_owner_id'],
                   'auth_generation': p['auth_generation'], 'model': model, 'image_mode': image_mode,
                   'execution_backend': 'local', 'access_mode': 'trusted_shared'}
        if actor_id != p['credential_owner_id']:
            grants = self.store.find('grant', profile_id=pid, actor_id=actor_id, enabled=True)
            if grants:
                binding.update(grant_id=grants[0]['id'], grant_version=grants[0]['version'])
        self.authorize(actor_id, binding)
        return binding

    def grant(self, owner_id, pid, actor_id, models):
        p = self.managed(owner_id, pid)
        if p['status'] != 'ready' or p.get('recovery_required'):
            raise HTTPException(409, '连接尚未就绪')
        if actor_id == owner_id or not active_user(actor_id):
            raise HTTPException(400, '请选择有效的 admin')
        allowed = {m['id'] for m in p['models']}
        if not models or not set(models) <= allowed:
            raise HTTPException(400, '请选择连接实际可用的模型')
        prior = self.store.find('grant', profile_id=pid, actor_id=actor_id)
        g = prior[0] if prior else {'id': new_id(), 'profile_id': pid, 'actor_id': actor_id, 'version': 0}
        g.update(version=g['version'] + 1, enabled=True, models=sorted(set(models)),
                 auth_generation=p['auth_generation'], updated_at=time.time(), granted_by=owner_id)
        return self.store.put('grant', g)

    async def revoke(self, owner_id, pid, grant_id):
        self.managed(owner_id, pid)
        g = self.store.get('grant', grant_id)
        if not g or g['profile_id'] != pid:
            raise HTTPException(404, '授权不存在')
        # Invalidate before waiting for shutdown; no new starts or late approvals.
        g.update(enabled=False, version=g['version'] + 1, updated_at=time.time())
        self.store.put('grant', g)
        if self.runs:
            await self.runs.cancel_profile(pid, g['actor_id'])
        return g

    def lock(self, pid):
        return self.locks.setdefault(pid, asyncio.Lock())

    def save_auth(self, p, data):
        provider = self.provider(p)
        who = provider.identity(data)
        # A second profile for the same account would bypass the per-profile
        # queue and race refresh-token rotation. Reuse the existing connection.
        for other in self.store.all('profile'):
            if other['id'] != p['id'] and other.get('runtime', 'codex') == p['runtime'] and other.get('identity') == who and (
                    self.vault.path(other['id']).exists() or other.get('recovery_required')):
                raise RuntimeFailure('此供应商账号已有连接，请使用该连接；更换连接前先断开旧连接')
        if p.get('identity') and p['identity'] != who:
            p['auth_generation'] += 1
        self.vault.write(p['id'], data, validator=provider.identity)
        p.update(identity=who, status='ready')
        self.store.put('profile', p)

    @contextlib.asynccontextmanager
    async def lease(self, binding, home: Path):
        pid = binding['profile_id']
        async with self.lock(pid):
            p = self.get(pid)
            if p['status'] != 'ready' or p['auth_generation'] != binding['auth_generation'] or p.get('recovery_required'):
                raise RuntimeFailure('连接已失效')
            home.mkdir(parents=True, exist_ok=True, mode=0o700)
            provider = self.provider(p)
            auth = provider.credential_path(home)
            try:
                data = self.vault.read(pid)
            except Exception as exc:
                p['status'] = 'error'
                self.store.put('profile', p)
                raise RuntimeFailure('无法读取登录缓存，请重新登录') from exc
            if provider.identity(data) != p['identity']:
                # Detect a crash between encrypted-vault replacement and metadata
                # commit BEFORE supplying a changed account to an existing grant.
                p.update(status='error', auth_generation=p['auth_generation'] + 1)
                self.store.put('profile', p)
                raise RuntimeFailure('登录缓存和账号记录不一致，请重新登录')
            p['active_lease'] = {'home': str(home.relative_to(self.store.root)), 'started_at': time.time()}
            self.store.put('profile', p)
            try:
                private_write(auth, data)
                yield
            finally:
                try:
                    latest = self.get(pid)
                    if latest['auth_generation'] == binding['auth_generation'] and not latest.get('recovery_required'):
                        if not auth.exists() or provider.identity(auth.read_bytes()) != p['identity']:
                            latest.update(status='disconnected', auth_generation=latest['auth_generation'] + 1)
                            self.store.put('profile', latest)
                            raise RuntimeFailure('供应商身份发生变化，连接已停用')
                        self.vault.write(pid, auth.read_bytes(), validator=provider.identity)
                except Exception:
                    latest = self.get(pid)
                    latest['status'] = 'error'
                    self.store.put('profile', latest)
                    raise
                finally:
                    auth.unlink(missing_ok=True)
                    latest = self.get(pid)
                    if not latest.get('recovery_required'):
                        latest.pop('active_lease', None)
                        self.store.put('profile', latest)

    async def start_login(self, actor_id, pid, mode):
        p = self.managed(actor_id, pid)
        provider = self.provider(p)
        if mode not in provider.modes:
            raise HTTPException(400, '此登录方式不适用于所选引擎')
        if p['status'] == 'disconnecting':
            raise HTTPException(409, '连接正在断开')
        if p.get('recovery_required'):
            raise HTTPException(409, '先由宿主检查并清理旧进程')
        if self.logins:
            raise HTTPException(409, '已有登录正在进行，请先完成或取消')
        attempt = {'id': new_id(), 'profile_id': pid, 'actor_id': actor_id, 'status': 'pending',
                   'created_at': time.time(), 'expires_at': time.time() + 900, 'challenge': None}
        # Persist the reservation before awaits so simultaneous login starts cannot race.
        entry = {'attempt': attempt, 'adapter': None, 'task': None}
        self.logins[attempt['id']] = entry
        p.update(status='connecting', login_id=attempt['id'])
        self.store.put('profile', p)
        self.store.put('login', attempt)
        home = self.store.root / 'login' / attempt['id']
        entry.update(home=home, provider=provider)
        try:
            home.mkdir(parents=True, mode=0o700, exist_ok=True)
            adapter = provider.adapter(home, home)
            entry['adapter'] = adapter
            if self.runs:
                await self.runs.cancel_profile(pid)
            async with self.lock(pid):
                await adapter.start()
                process = getattr(getattr(adapter, 'rpc', None), 'process', None)
                if process:
                    attempt['process_pid'] = process.pid
                    self.store.put('login', attempt)
                response = await provider.login_start(adapter, mode)
                process = getattr(getattr(adapter, 'rpc', None), 'process', None)
                if process:
                    attempt['process_pid'] = process.pid
                    self.store.put('login', attempt)
            url = response.get('authUrl') or response.get('verificationUrl')
            parsed = urlsplit(url or '')
            if parsed.scheme != 'https' or parsed.hostname not in provider.login_hosts or parsed.username or parsed.password:
                raise RuntimeFailure('供应商登录地址无效')
            attempt['provider_login_id'] = response['loginId']
            attempt['challenge'] = {'url': url, 'user_code': response.get('userCode'), 'mode': mode}
            self.store.put('login', attempt)
            entry['task'] = asyncio.create_task(self._watch_login(entry))
        except BaseException:
            await self._cleanup_login(entry, 'failed')
            raise
        return self.public_login(attempt)

    def public_login(self, attempt):
        return {k: attempt.get(k) for k in ('id', 'profile_id', 'status', 'expires_at', 'challenge')}

    async def close_adapter(self, pid, adapter):
        try:
            await adapter.close()
        except BaseException:
            latest = self.get(pid)
            latest['recovery_required'] = True
            rpc = getattr(adapter, 'rpc', None)
            process = getattr(rpc, 'process', None)
            pids = set(latest.get('recovery_pids', []))
            if process:
                pids.add(process.pid)
            pids.update(getattr(getattr(rpc, 'process_tree', None), 'seen', {}))
            latest['recovery_pids'] = sorted(pids)
            self.store.put('profile', latest)
            raise

    async def _cleanup_login(self, entry, status):
        try:
            if not entry.get('closed'):
                if entry.get('adapter') is not None:
                    await self.close_adapter(entry['attempt']['profile_id'], entry['adapter'])
                entry['closed'] = True
        except BaseException:
            entry['attempt']['cleanup_required'] = True
            status = 'failed'
        finally:
            self._finish_login(entry, status)

    def _finish_login(self, entry, status):
        a = entry['attempt']
        a.update(status=status, challenge=None)
        self.store.put('login', a)
        p = self.get(a['profile_id'])
        if p.get('login_id') == a['id']:
            p['login_id'] = None
            if p['status'] == 'connecting':
                p['status'] = 'error' if a.get('cleanup_required') else ('ready' if self.vault.path(p['id']).exists() else 'disconnected')
            self.store.put('profile', p)
        if entry.get('home') and not a.get('cleanup_required'):
            shutil.rmtree(entry['home'], ignore_errors=True)
        self.logins.pop(a['id'], None)

    async def _watch_login(self, entry):
        a, adapter = entry['attempt'], entry['adapter']
        status = 'failed'
        try:
            async with asyncio.timeout(max(1, a['expires_at'] - time.time())):
                provider = entry['provider']
                await provider.login_wait(adapter, a['provider_login_id'])
                self.owner(a['actor_id'])
                account, models = await provider.account_models(adapter)
                await self.close_adapter(a['profile_id'], adapter)
                entry['closed'] = True
                async with self.lock(a['profile_id']):
                    p = self.get(a['profile_id'])
                    if p.get('login_id') != a['id']:
                        return
                    data = provider.credential_path(entry['home']).read_bytes()
                    p.update(account=account, models=models)
                    self.save_auth(p, data)
                status = 'completed'
        except asyncio.CancelledError:
            status = 'cancelled'
        except TimeoutError:
            status = 'expired'
        except Exception:
            status = 'failed'
        finally:
            await self._cleanup_login(entry, status)

    async def cancel_login(self, actor_id, pid, login_id):
        p = self.managed(actor_id, pid)
        if p.get('login_id') != login_id:
            raise HTTPException(409, '登录尝试已失效')
        entry = self.logins.get(login_id)
        if entry:
            if entry.get('adapter') and entry['attempt'].get('provider_login_id'):
                with contextlib.suppress(Exception):
                    await entry['provider'].login_cancel(entry['adapter'], entry['attempt']['provider_login_id'])
            if entry['task']:
                entry['task'].cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await entry['task']
                if login_id in self.logins:
                    await self._cleanup_login(entry, 'cancelled')
            else:
                raise HTTPException(409, '正在创建登录，请稍后再取消')

    async def probe(self, actor_id, pid):
        p = self.managed(actor_id, pid)
        if p['status'] != 'ready' or p.get('recovery_required'):
            raise HTTPException(409, '请先登录连接')
        if self.runs and any(s['run']['binding']['profile_id'] == pid for s in self.runs.active.values()):
            raise HTTPException(409, '连接正在执行任务，请稍后刷新')
        backend = self.runs.backend if self.runs else None
        if backend and hasattr(backend, 'pause'):
            try:
                await backend.pause(pid)
            except BaseException:
                backend.resume(pid)
                raise
        elif backend and hasattr(backend, 'release'):
            await backend.release(profile_id=pid)
        home = self.store.root / 'probe' / new_id()
        home.mkdir(parents=True, mode=0o700)
        binding = {'profile_id': pid, 'auth_generation': p['auth_generation']}
        try:
            async with self.lease(binding, home):
                provider = self.provider(p)
                adapter = provider.adapter(home, home)
                try:
                    await adapter.start()
                    account, models = await provider.account_models(adapter, refresh=True)
                    latest = self.get(pid)
                    if latest['status'] != 'ready' or latest['auth_generation'] != binding['auth_generation']:
                        raise HTTPException(409, '连接状态已改变')
                    if not account.get('email') and latest.get('account'):
                        account['email'] = latest['account'].get('email')
                    latest.update(account=account, models=models)
                    self.store.put('profile', latest)
                finally:
                    await self.close_adapter(pid, adapter)
        finally:
            if not self.get(pid).get('recovery_required'):
                shutil.rmtree(home, ignore_errors=True)
            if backend and hasattr(backend, 'resume'):
                backend.resume(pid)
        return self.public(self.get(pid), actor_id)

    async def disconnect(self, actor_id, pid):
        p = self.managed(actor_id, pid)
        if p.get('login_id'):
            await self.cancel_login(actor_id, pid, p['login_id'])
        p = self.get(pid)
        p.update(status='disconnecting', auth_generation=p['auth_generation'] + 1)
        self.store.put('profile', p)
        if self.runs:
            await self.runs.cancel_profile(pid)
        async with self.lock(pid):
            # Shutdown can fence this profile; never overwrite its latest state.
            p = self.get(pid)
            self.vault.delete(pid)
            p.update(status='disconnected', account=None, models=[], login_id=None)
            self.store.put('profile', p)
        return self.public(p, actor_id)

    async def shutdown(self):
        for entry in list(self.logins.values()):
            if entry['task']:
                entry['task'].cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await entry['task']
            if entry['attempt']['id'] in self.logins:
                await self._cleanup_login(entry, 'cancelled')
