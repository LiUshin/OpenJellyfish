#!/usr/bin/env python3
"""Serve the synthetic acceptance dataset on loopback; never a production launcher."""
import argparse
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
parser = argparse.ArgumentParser()
parser.add_argument('--root', required=True)
parser.add_argument('--codex', required=True)
parser.add_argument('--port', type=int, default=8002)
args = parser.parse_args()
root = Path(args.root).resolve()
if not (root / 'users' / 'users.json').exists():
    raise SystemExit('Run the explicit acceptance fixture first')
os.environ.update(JELLYFISH_RUNTIME_ENABLED='1', JELLYFISH_OWNER_USER_ID='bench_owner',
                  JELLYFISH_RUNTIME_DATA_DIR=str(root/'runtime'), JELLYFISH_RUNTIME_CODEX_BIN=args.codex,
                  DISABLE_SCHEDULER='1', DISABLE_WECHAT_CHANNEL='1', RESTORE_VENVS_ON_STARTUP='0', STORAGE_BACKEND='local')
from app.core import security
from app.storage import local
security.USERS_DIR = local.USERS_DIR = str(root/'users')
security.USERS_JSON = str(root/'users'/'users.json')
import uvicorn
uvicorn.run('app.main:app', host='127.0.0.1', port=args.port, access_log=False)
