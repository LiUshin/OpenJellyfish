"""Small durable projection of provider events into Jellyfish message blocks."""


def append_event(blocks, kind, payload):
    if kind == 'notice':
        blocks.append({'type': 'text', 'content': '\n\n提示：' + str(payload.get('message', ''))})
    elif kind == 'text_delta':
        text = payload.get('text', '')
        if blocks and blocks[-1]['type'] == 'text':
            blocks[-1]['content'] += text
        elif text:
            blocks.append({'type': 'text', 'content': text})
    elif kind in ('tool', 'business_tool'):
        key = payload.get('item_id') or payload.get('name') or f'tool-{len(blocks)}'
        block = next((b for b in reversed(blocks) if b.get('event_key') == key), None)
        # Repeated business calls share a name but are distinct calls.
        if block and kind == 'business_tool' and payload.get('status') == 'running' and block['done']:
            block = None
        if block is None:
            block = {'type': 'tool', 'event_key': key, 'name': payload.get('name') or (payload.get('command') if payload.get('kind') == 'other' else None) or payload.get('kind') or '工具',
                     'args': '', 'result': '', 'done': False, 'resultCollapsed': True}
            block['name'] = {'webSearch': '网页搜索', 'search': '网页搜索', 'imageGeneration': '生成图片', 'fetch': '读取网页'}.get(block['name'], block['name'])
            blocks.append(block)
        if payload.get('command'):
            block['args'] = str(payload['command'])[:8000]
        status = payload.get('status')
        if status in ('completed', 'failed', 'cancelled', 'declined'):
            block['done'] = True
            block['result'] = {'completed': '已完成', 'failed': '失败', 'cancelled': '已取消', 'declined': '已拒绝'}[status]
    elif kind in ('completed', 'failed', 'cancelled'):
        for block in blocks:
            if block['type'] == 'tool' and not block['done']:
                block.update(done=True, result='本轮已结束；未收到工具完成事件')
