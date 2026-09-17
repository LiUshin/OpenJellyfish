"""Read-only protocol/auth probe; does not log in, execute a turn, or expose account data."""
import argparse
import asyncio
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.runtime.codex import CodexAdapter


async def probe(executable, home):
    root = Path(home).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    adapter = CodexAdapter(executable, root, root)
    try:
        print(json.dumps(await adapter.probe(), ensure_ascii=False, indent=2))
    finally:
        await adapter.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--codex', default='codex')
    parser.add_argument('--home', required=True, help='Dedicated Codex identity directory; never copied from another profile')
    args = parser.parse_args()
    executable = shutil.which(args.codex)
    if not executable:
        parser.error('Codex executable not found')
    asyncio.run(probe(executable, args.home))
