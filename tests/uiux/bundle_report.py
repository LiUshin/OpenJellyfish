"""Compare static ESM dependency closures, including shared chunks (not just entry sizes).
Usage: python3 tests/uiux/bundle_report.py /path/to/baseline/frontend/dist frontend/dist
This is a transfer-size proxy, not an LCP/INP measurement or a browser trace.
"""
from pathlib import Path
import re, json, gzip, sys

def report(directory):
    root = Path(directory)
    html = (root / 'index.html').read_text()
    main = root / re.search(r'<script[^>]+src="([^"]+)', html)[1].lstrip('/')
    def closure(entries):
        visited = set()
        def visit(path):
            path = path.resolve()
            if path in visited: return
            visited.add(path)
            source = path.read_text()
            # Rollup emits local static imports as import{...}from"./x.js" or import"./x.js".
            for relative in re.findall(r'(?:\bfrom\s*|\bimport\s*)["\'](\./[^"\']+\.js)["\']', source):
                visit(path.parent / relative)
        for entry in entries: visit(entry)
        return {'raw_bytes': sum(p.stat().st_size for p in visited),
                'gzip_bytes': sum(len(gzip.compress(p.read_bytes(), mtime=0)) for p in visited),
                'chunks': sorted(p.name for p in visited)}
    # Chat route bundling has multiple index chunks; identify by a stable translation key.
    chat = [p for p in (root / 'assets').glob('*.js') if 'chat.newChat' in p.read_text()]
    shell = list((root / 'assets').glob('AppLayout-*.js'))
    login = list((root / 'assets').glob('Login-*.js'))
    return {'login': closure([main, *login]), 'workspace': closure([main, *shell, *chat])}

if __name__ == '__main__':
    print(json.dumps({str(path): report(path) for path in sys.argv[1:]}, indent=2))
