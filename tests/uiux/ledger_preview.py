"""Offline Run/outbox UI fixture; no executors, credentials, or network sends."""
from http.server import ThreadingHTTPServer
from urllib.parse import urlparse
from scheduler_preview import SchedulerHandler, TASK, WRITES

TASK.update(task_type='agent', recovery_required=True,
            task_config={'prompt':'Offline run and outbox verification'})
RUNS=[
    {'run_id':'run_success','status':'success','output':'Saved result; delivery has independent status',
     'started_at':'2026-09-22T00:00:00+00:00','steps':[{'type':'finish','content':'DURABLE_RESULT'}],
     'deliveries':[{'id':'delivery_retry','channel':'web','status':'failed','attempt':5,'error':'Fixture failure'},
                   {'id':'delivery_unknown','channel':'wechat','status':'unknown','attempt':1,'error':'Fixture lost acknowledgement'}]},
    {'run_id':'run_interrupted','status':'interrupted','output':'Review external effects before resuming',
     'started_at':'2026-09-22T01:00:00+00:00','steps':[]},
]

class LedgerHandler(SchedulerHandler):
    def do_GET(self):
        if urlparse(self.path).path=='/api/scheduler/task_fixture/runs':
            return self.send_json(RUNS)
        return super().do_GET()

    def do_POST(self):
        path=urlparse(self.path).path
        if path=='/api/scheduler/deliveries/delivery_retry/retry':
            WRITES.append({'action':'retry','id':'delivery_retry'})
            RUNS[0]['deliveries'][0]['status']='pending'
            return self.send_json({'success':True})
        if path=='/api/scheduler/runs/run_interrupted/resolve-recovery':
            body=self.body(); WRITES.append({'action':'recovery',**body})
            TASK.update(recovery_required=False,enabled=False,next_run_at=None)
            return self.send_json({'success':True})
        return super().do_POST()

if __name__=='__main__':
    print('OFFLINE LEDGER UI — http://127.0.0.1:8769',flush=True)
    ThreadingHTTPServer(('127.0.0.1',8769),LedgerHandler).serve_forever()
