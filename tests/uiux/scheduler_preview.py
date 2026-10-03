"""Offline scheduling UI regression fixture, http://127.0.0.1:8769.

Extends preview.py. No real account, model calls or scheduled execution.
The last PATCH body is inspectable at /__fixture/scheduler-write.
"""
from http.server import ThreadingHTTPServer
from urllib.parse import urlparse
from preview import Handler

TASK = {
    'id': 'task_fixture', 'name': 'Scheduler regression', 'description': 'Offline fixture',
    'task_type': 'script', 'schedule_type': 'interval', 'schedule': '3600',
    'enabled': True, 'tz_offset_hours': -5, 'run_count': 25,
    'created_at': '2026-09-21T00:00:00+00:00',
    'next_run_at': '2027-01-01T00:00:00+00:00',
    'task_config': {'script_path': 'check.py', 'timeout': 97, 'input_data': 'KEEP_INPUT',
                    'future_option': {'preserve': True}},
    'runs': [{'run_id': 'run_fixture', 'status': 'success', 'output': 'Offline result',
              'started_at': '2026-09-21T00:00:00+00:00', 'finished_at': '2026-09-21T00:00:01+00:00'}],
}
WRITES = []


class SchedulerHandler(Handler):
    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/api/scheduler':
            return self.send_json([{k: v for k, v in TASK.items() if k != 'runs'}])
        if path == '/api/scheduler/task_fixture':
            return self.send_json(TASK)
        if path == '/api/scheduler/task_fixture/runs':
            return self.send_json([{**TASK['runs'][0], 'steps': [{'type': 'stdout', 'content': 'FULL_STEPS_LOADED'}]}])
        if path == '/__fixture/scheduler-write':
            return self.send_json(WRITES)
        return super().do_GET()

    def do_PUT(self):
        if urlparse(self.path).path == '/api/scheduler/task_fixture':
            body = self.body()
            WRITES.append(body)
            TASK.update(body)
            return self.send_json(TASK)
        return super().do_PUT()

    def do_POST(self):
        if urlparse(self.path).path == '/api/scheduler/task_fixture/run-now':
            return self.send_json({'detail': '任务未触发：调度已停用、队列已满或任务正在运行'}, 409)
        return super().do_POST()


if __name__ == '__main__':
    print('OFFLINE SCHEDULER UI — http://127.0.0.1:8769', flush=True)
    ThreadingHTTPServer(('127.0.0.1', 8769), SchedulerHandler).serve_forever()
