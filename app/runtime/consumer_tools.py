"""Service-bound tools. Supplier requests cannot choose tenants or conversations."""
import asyncio
import contextlib
import json
import tempfile
from pathlib import Path, PurePosixPath

from fastapi import HTTPException
from pydantic import Field

from app.runtime.business_tools import Empty, Document, Directory, BusinessTools
from app.runtime.files import MAX_TOTAL


class History(Empty):
    last_n: int = Field(default=20, ge=1, le=100)


class WriteFile(Document):
    content: str = Field(max_length=65536)


class Script(Empty):
    script_path: str = Field(min_length=1, max_length=500)
    script_args: list[str] = Field(default_factory=list, max_length=30)
    input_data: str = Field(default='', max_length=32000)
    timeout: int = Field(default=30, ge=1, le=60)


TOOLS = {
    'list_documents': (Directory, '列出本 Service 开放的 /docs 文档目录；只显示白名单范围。'),
    'read_document': (Document, '读取本 Service 白名单内的文档，支持文本、PDF、Word、Excel，单文件最多 8 MB。'),
    'read_my_conversation': (History, '读取当前 Service 对话的最近消息，不包含其他对话或 admin 长期记忆。'),
    'list_files': (Empty, '列出当前 Service 对话生成的文件。'),
    'read_file': (Document, '读取当前对话 /generated 内的 UTF-8 文本，最多 256KB。'),
    'write_file': (WriteFile, '在当前对话 /generated 内保存文本文件，返回可展示的文件标签。'),
    'run_script': (Script, '执行 admin 已向此 Service 开放的脚本；仅挂载白名单内的文档与脚本，产物写入本对话。'),
}


def relative(raw, namespace):
    p = PurePosixPath(raw)
    if not raw or '\\' in raw or any(c in raw for c in ('\x00', '\r', '\n')) or any(v.startswith('.') for v in p.parts if v != '/'):
        raise ValueError('路径无效')
    clean = raw.lstrip('/')
    prefix = namespace + '/'
    return clean[len(prefix):] if clean.startswith(prefix) else clean


def allowed(path, patterns, *, directory=False):
    return any(p == '*' or path == p.strip('/') or path.startswith(p.strip('/') + '/') or
               (directory and (not path or p.strip('/').startswith(path + '/')))
               for p in patterns if p.strip('/'))


class ServiceTools:
    def __init__(self, storage, store, authorize):
        self.storage, self.store, self.authorize = storage, store, authorize

    def config(self, actor, binding):
        self.authorize(actor, binding)
        from app.services.published import get_service
        return get_service(actor, binding['service_scope']['service_id'])

    def specifications(self, binding, actor):
        svc = self.config(actor, binding)
        return [{'type': 'function', 'name': 'jellyfish_service_' + name,
                 'description': desc, 'inputSchema': schema.model_json_schema(), 'deferLoading': False}
                for name, (schema, desc) in TOOLS.items() if name != 'run_script' or svc.get('allowed_scripts')]

    def checked_path(self, actor, namespace, raw, patterns, directory=False):
        path = relative(raw, namespace)
        if path == namespace and directory:
            path = ''
        if not allowed(path, patterns, directory=directory):
            raise ValueError('文件未向此 Service 开放')
        # LocalStorage resolves symlinks; reject them BEFORE access, including
        # links between two paths within the admin's root that cross the allowlist.
        from app.storage.local import LocalStorageService
        if isinstance(self.storage, LocalStorageService):
            from app.core.security import get_user_filesystem_dir
            current = Path(get_user_filesystem_dir(actor))
            for part in (namespace, *PurePosixPath(path).parts):
                current = current / part
                if current.is_symlink():
                    raise ValueError('Service 不读取符号链接')
        return '/' + namespace + ('/' + path if path else '')

    def tree(self, actor, namespace, patterns, root=None):
        pending, result, scanned = [root or '/' + namespace], [], 0
        while pending:
            scanned += 1
            if scanned > 200:
                raise ValueError('开放目录过多，请缩小 Service 白名单')
            parent = pending.pop()
            for entry in self.storage.list_dir(actor, parent):
                path = parent + '/' + entry.name
                try:
                    self.checked_path(actor, namespace, path, patterns, directory=entry.is_dir)
                except ValueError:
                    continue
                if entry.is_dir:
                    pending.append(path)
                else:
                    result.append((path, entry))
                if len(pending) + len(result) > 200:
                    raise ValueError('开放文件超过 200 项，请缩小 Service 白名单')
        return result

    async def __call__(self, session, run, params):
        actor, binding = session['actor_id'], session['binding']
        svc = self.config(actor, binding)
        name = params.get('tool', '').removeprefix('jellyfish_service_')
        if params.get('tool') != 'jellyfish_service_' + name or name not in TOOLS:
            return BusinessTools.result('此业务工具未向 Service 开放', False)
        try:
            args = TOOLS[name][0].model_validate(params.get('arguments', {}))
            self.store.emit(run, 'business_tool', {'name': params['tool'], 'status': 'running'})
            if name == 'run_script':
                # Wait for the bounded runner to terminate even if the caller
                # disconnects; don't release the profile with a script still live.
                job = asyncio.create_task(asyncio.to_thread(self.script, actor, binding, svc, args))
                try:
                    result = await asyncio.shield(job)
                except asyncio.CancelledError:
                    with contextlib.suppress(Exception):
                        await job
                    raise
            elif name == 'write_file':
                result = self.invoke(actor, binding, svc, name, args)
            else:
                # Remote storage and PDF extraction must not block other streams.
                result = await asyncio.to_thread(self.invoke, actor, binding, svc, name, args)
            self.authorize(actor, binding)
            if name == 'run_script':
                result, files = result
                scope = binding['service_scope']
                for rel, data, *_ in files:
                    self.authorize(actor, binding)
                    self.storage.write_consumer_bytes(actor, scope['service_id'], scope['conversation_id'], rel, data)
                result = {**result, 'files': [f'<<FILE:/generated/{rel}>>' for rel, *_ in files]}
            text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
            self.store.emit(run, 'business_tool', {'name': params['tool'], 'status': 'completed', 'result': text[:500]})
            return BusinessTools.result(text[:256000], True)
        except HTTPException:
            raise
        except (ValueError, OSError, PermissionError, UnicodeError):
            self.store.emit(run, 'business_tool', {'name': params['tool'], 'status': 'failed'})
            return BusinessTools.result('工具拒绝执行：请检查文件路径、白名单、文件格式与大小。', False)

    def invoke(self, actor, binding, svc, name, args):
        scope = binding['service_scope']
        identity = (actor, scope['service_id'], scope['conversation_id'])
        if name == 'list_documents':
            patterns = svc.get('allowed_docs') or []
            if not patterns:
                return []
            root = self.checked_path(actor, 'docs', args.path, patterns, directory=True)
            result = []
            for entry in self.storage.list_dir(actor, root):
                try:
                    path = self.checked_path(actor, 'docs', root + '/' + entry.name, patterns, directory=entry.is_dir)
                except ValueError:
                    continue
                result.append({'path': path, 'is_dir': entry.is_dir, 'size': entry.size})
            return result[:200]
        if name == 'read_document':
            path = self.checked_path(actor, 'docs', args.path, svc.get('allowed_docs') or [])
            entries = self.storage.list_dir(actor, str(PurePosixPath(path).parent))
            entry = next((e for e in entries if e.name == PurePosixPath(path).name and not e.is_dir), None)
            if not entry or entry.size > 8 * 1024 * 1024:
                raise ValueError('文档超过 8 MB 或不存在')
            data = self.storage.read_bytes(actor, path)
            if len(data) > 8 * 1024 * 1024:
                raise ValueError('文档超过 8 MB')
            ext = PurePosixPath(path).suffix.lower()
            if ext in ('.pdf', '.docx', '.xlsx'):
                from app.services.document_tools import _extract_pdf_text, _extract_docx_text, _extract_xlsx_text
                with tempfile.NamedTemporaryFile(suffix=ext) as f:
                    f.write(data); f.flush()
                    return {'.pdf': _extract_pdf_text, '.docx': _extract_docx_text, '.xlsx': _extract_xlsx_text}[ext](f.name)
            return data.decode('utf-8')[:256000]
        if name == 'read_my_conversation':
            from app.services.published import get_consumer_conversation
            conv = get_consumer_conversation(*identity)
            return [{'role': m.get('role'), 'content': m.get('content')} for m in conv.get('messages', [])[-args.last_n:]]
        if name == 'list_files':
            return self.storage.list_consumer_files(*identity)[:200]
        if name in ('read_file', 'write_file'):
            path = relative(args.path, 'generated')
            if not path or any(c in path for c in '<>'):
                raise ValueError('文件名无效')
            from app.storage.local import LocalStorageService
            if isinstance(self.storage, LocalStorageService):
                from app.services.published import get_consumer_generated_dir
                current = Path(get_consumer_generated_dir(*identity))
                for part in PurePosixPath(path).parts:
                    current = current / part
                    if current.is_symlink():
                        raise ValueError('产物不可为符号链接')
            if name == 'read_file':
                info = next((f for f in self.storage.list_consumer_files(*identity) if f['path'] == path), None)
                if not info or info['size'] > 256000:
                    raise ValueError('文件不存在或过大')
                return self.storage.read_consumer_bytes(*identity, path)[:256000].decode('utf-8')
            self.storage.write_consumer_bytes(*identity, path, args.content.encode())
            return '<<FILE:/generated/' + path + '>>'
        raise ValueError('工具不可用')

    def script(self, actor, binding, svc, args):
        # No authorization/store access on this worker thread (SQLite is owned
        # by the scheduler). The bridge checks authorization before and after.
        path = self.checked_path(actor, 'scripts', args.script_path, svc.get('allowed_scripts') or [])
        from app.services.script_runner import run_script
        from app.services.venv_manager import get_user_python
        with tempfile.TemporaryDirectory(prefix='jf-service-script-') as tmp:
            root = Path(tmp)
            for directory in ('docs', 'scripts', 'generated'):
                (root / directory).mkdir()
            total = 0
            for ns, patterns in [('docs', svc.get('allowed_docs') or []), ('scripts', svc.get('allowed_scripts') or [])]:
                if not patterns:
                    continue
                for source, entry in self.tree(actor, ns, patterns):
                    if entry.size > 8 * 1024 * 1024:
                        raise ValueError('脚本输入文件过大')
                    data = self.storage.read_bytes(actor, source)
                    total += len(data)
                    if total > MAX_TOTAL:
                        raise ValueError('脚本输入总量过大')
                    target = root / source.lstrip('/')
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
            result = run_script(script_path=path.removeprefix('/scripts/'), scripts_dir=str(root / 'scripts'),
                                input_data=args.input_data, args=args.script_args, timeout=args.timeout,
                                allowed_read_dirs=[str(root / 'docs')], allowed_write_dirs=[str(root / 'generated')],
                                python_executable=get_user_python(actor), unrestricted=False)
            from app.runtime.files import collect_files
            return result, collect_files(root / 'generated', {}, [])
