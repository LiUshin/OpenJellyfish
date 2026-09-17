"""Provider differences; profiles own authorization, serialization and cleanup."""
from pathlib import Path
from app.runtime.codex import CodexAdapter
from app.runtime.rpc import RuntimeFailure
from app.runtime.vault import identity


class CodexProvider:
    runtime = 'codex'
    modes = {'chatgpt', 'chatgptDeviceCode'}
    login_hosts = {'auth.openai.com', 'auth0.openai.com', 'chatgpt.com'}
    identity = staticmethod(identity)

    def __init__(self, executable, adapter_factory=CodexAdapter):
        self.executable, self.adapter_factory = executable, adapter_factory

    def adapter(self, home, cwd, model=None, dynamic_tools=None):
        return self.adapter_factory(self.executable, home, cwd, model, managed=True, dynamic_tools=dynamic_tools)

    @staticmethod
    def credential_path(home):
        return Path(home) / 'auth.json'

    async def login_start(self, adapter, mode):
        return await adapter.rpc.request('account/login/start', {'type': mode}, timeout=90)

    async def login_wait(self, adapter, login_id):
        while True:
            event = await adapter.rpc.next_event()
            params = event.get('params', {})
            if event.get('method') == 'account/login/completed' and params.get('loginId') == login_id:
                if not params.get('success'):
                    raise RuntimeFailure('供应商登录未完成')
                return

    async def login_cancel(self, adapter, login_id):
        await adapter.rpc.request('account/login/cancel', {'loginId': login_id}, timeout=5)

    async def account_models(self, adapter, refresh=False):
        account = await adapter.rpc.request('account/read', {'refreshToken': refresh})
        models = await adapter.rpc.request('model/list', {})
        info = account.get('account') or {}
        if info.get('type') != 'chatgpt':
            raise RuntimeFailure('此连接需要 ChatGPT 个人登录')
        return ({k: info.get(k) for k in ('email', 'planType')},
                [{'id': m['id'], 'name': m.get('displayName', m['id'])} for m in models.get('data', [])])


class CursorProvider:
    runtime = 'cursor'
    modes = {'cursorBrowser'}
    login_hosts = {'cursor.com', 'www.cursor.com'}

    def __init__(self, executable):
        self.executable = executable

    def adapter(self, home, cwd, model=None, dynamic_tools=None):
        from app.runtime.cursor import CursorAdapter
        return CursorAdapter(self.executable, home, cwd, model, dynamic_tools=dynamic_tools)

    @staticmethod
    def identity(data):
        from app.runtime.cursor import cursor_identity
        return cursor_identity(data)

    @staticmethod
    def credential_path(home):
        from app.runtime.cursor import credential_path
        return credential_path(home)

    async def login_start(self, adapter, mode):
        return await adapter.login_start()

    async def login_wait(self, adapter, login_id):
        await adapter.login_wait()

    async def login_cancel(self, adapter, login_id):
        pass  # Shared cleanup closes the complete login process tree.

    async def account_models(self, adapter, refresh=False):
        return await adapter.account_models()
