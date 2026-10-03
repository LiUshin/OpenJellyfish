"""Persisted maximum grant intersected with current owner/task/ancestor/service policy.

This is an application-tool boundary, not an OS sandbox. Unadapted native-code
and CLI executors are rejected instead of claiming that Python hooks isolate them.
"""
import json
import os
from pathlib import PurePosixPath
from app.execution import context
from app.services.scheduler_policy import permission_dirs, validate_reply

SUPPORTED_CAPABILITIES = {'docs', 'documents', 'humanchat', 'scheduler', 'web', 'image'}
SERVICE_SUPPORTED_CAPABILITIES = {'docs', 'documents', 'humanchat', 'scheduler'}


def validate_service_task_support(uid, sid, config):
    """Reject unsupported executors/capabilities before accepting a fan-out."""
    from app.channels.wechat.policy import ensure_service_active
    from fastapi import HTTPException
    from app.runtime.consumer import external
    try:
        service = ensure_service_active(uid, sid)
    except HTTPException as exc:
        raise PermissionError(str(exc.detail)) from exc
    if external(service):
        raise PermissionError('该 Service 的 Codex/Cursor 执行器尚未接入定时授权适配')
    unsupported = set(config.get('capabilities') or []) - SERVICE_SUPPORTED_CAPABILITIES
    if unsupported:
        raise PermissionError('定时任务尚不支持这些能力：' + ', '.join(sorted(unsupported)))
    return service


def capture(task, uid, sid=None):
    cfg = task.get('task_config') or {}
    perms = cfg.get('permissions') or {}
    grant = {'read_dirs': list(perms.get('read_dirs', ['docs', 'scripts', 'generated'])),
             'write_dirs': list(perms.get('write_dirs', ['generated'])),
             'capabilities': list(cfg.get('capabilities') or [])}
    if sid:
        from app.services.published import get_service
        service = get_service(uid, sid) or {}
        grant['service_docs'] = list(service.get('allowed_docs') or [])
        grant['service_capabilities'] = list(service.get('capabilities') or [])
        if 'capabilities' not in cfg:
            grant['capabilities'] = list(grant['service_capabilities'])
    return grant


def canonical(path):
    if not isinstance(path, str) or '\x00' in path:
        raise PermissionError('Invalid execution path')
    parts = PurePosixPath(path.replace('\\', '/').lstrip('/')).parts
    if '..' in parts:
        raise PermissionError('Path traversal is not permitted')
    return '/'.join(parts)


class Grant:
    def __init__(self, execution=None):
        self.execution = execution or context.current()
        if not self.execution:
            raise PermissionError('Durable execution context is required')
        self.run = self.execution.run
        self.scope, self.uid, self.sid, self.tid = json.loads(self.run['task_key'])
        self.snapshot = self.run['snapshot']
        self.saved = self.snapshot['execution_grant']
        self.conv = (self.snapshot.get('reply_to') or {}).get('conversation_id', f'sched-{self.tid}')

    def policies(self):
        self.execution.check()
        from app.core.security import _load_users
        from app.services import scheduler_tree as st
        owner = _load_users().get(self.uid)
        if not owner or owner.get('disabled'):
            raise PermissionError('Execution owner is disabled or missing')
        task = st.load_task_or_migrate(self.scope, self.uid, self.tid, self.sid)
        if not task or (not task.get('enabled') and task.get('revision') != self.snapshot.get('revision')):
            raise PermissionError('Execution task was deleted or paused')
        validate_reply(self.snapshot.get('reply_to'), self.uid, self.sid, check_session=False)
        if task.get('reply_to') != self.snapshot.get('reply_to'):
            raise PermissionError('Execution delivery target was changed')
        policies = [self.saved, capture(task, self.uid, self.sid)]
        for ancestor in self.snapshot.get('spawn_chain') or []:
            parent = st.load_task_or_migrate(self.scope, self.uid, ancestor, self.sid)
            if not parent:
                raise PermissionError('Parent grant no longer exists')
            policies.append(capture(parent, self.uid, self.sid))
        if self.sid:
            validate_service_task_support(self.uid, self.sid, self.snapshot.get('task_config') or {})
            reply = self.snapshot.get('reply_to') or {}
            if reply.get('channel') == 'wechat':
                from app.channels.wechat.policy import ensure_wechat_session
                from fastapi import HTTPException
                try:
                    ensure_wechat_session(self.uid, self.sid, reply.get('session_id', ''),
                                          conversation_id=reply.get('conversation_id'))
                except HTTPException as exc:
                    raise PermissionError(str(exc.detail)) from exc
        return policies

    def capability(self, name):
        policies = self.policies()
        if name not in SUPPORTED_CAPABILITIES:
            raise PermissionError(f'Capability {name} has no scheduled grant adapter')
        if name not in ('docs', 'documents', 'humanchat'):
            for policy in policies:
                if name not in policy['capabilities'] or (self.sid and name not in policy['service_capabilities']):
                    raise PermissionError(f'Capability {name} is not currently granted')

    def path(self, path, *, write=False):
        from app.core.security import get_user_filesystem_dir
        from app.core.path_security import safe_join
        clean = canonical(path)
        root = get_user_filesystem_dir(self.uid)
        policies = self.policies()
        # Check both logical and canonical paths: symlinks cannot broaden a grant.
        full = safe_join(root, clean)
        for policy in policies:
            names = policy['write_dirs' if write else 'read_dirs']
            bases = permission_dirs(root, names)
            if not any(full == base or full.startswith(base + os.sep) for base in bases):
                raise PermissionError(f'Path is outside the current execution grant: {clean}')
        if self.sid:
            if clean.startswith('docs/') and not write:
                for policy in policies:
                    base = os.path.join(root, 'docs')
                    grants = permission_dirs(base, policy['service_docs'])
                    if not any(full == g or full.startswith(g + os.sep) for g in grants):
                        raise PermissionError('Document is not published to this service')
            elif clean.startswith('generated/'):
                # Generated writes are namespaced to this consumer conversation by the adapter.
                from app.services.published import get_consumer_generated_dir
                safe_join(get_consumer_generated_dir(self.uid, self.sid, self.conv), clean[len('generated/'):])
            else:
                raise PermissionError('Service access is limited to published docs and conversation output')
        return clean

    def child(self, data):
        self.capability('scheduler')
        cfg = dict(data.get('task_config') or {})
        supplied = cfg.get('permissions')
        if supplied is None:
            cfg['permissions'] = {key: list(self.saved[key]) for key in ('read_dirs', 'write_dirs')}
        else:
            for key in ('read_dirs', 'write_dirs'):
                # Omitted lists must not obtain broader defaults downstream.
                supplied.setdefault(key, list(self.saved[key]))
                for name in supplied[key]:
                    self.path('' if name == '*' else name, write=key == 'write_dirs')
        cfg.setdefault('capabilities', list(self.saved['capabilities']))
        for cap in cfg['capabilities']:
            if cap not in self.saved['capabilities']:
                raise PermissionError('Child cannot broaden parent capabilities')
            self.capability(cap)
        if data.get('task_type', 'agent') != 'agent':
            raise PermissionError('Child script executor has no enforced grant adapter')
        return {**data, 'task_config': cfg}
