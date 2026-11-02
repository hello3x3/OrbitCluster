#!/usr/bin/env python3
"""Administrator delivery artifact. Only explicit source/resource trees, never runtime data."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

PORTAL = Path(__file__).resolve().parents[1]
ROOT = PORTAL.parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/orbitcluster-portal-cli-0.2.0.zip')
    args = parser.parse_args()
    release = PORTAL / 'terminal-downloads'
    manifest = json.loads((release / 'manifest.json').read_text())
    if len(manifest['artifacts']) != 6:
        raise SystemExit('先在 terminal/ 执行 go run ./cmd/release，生成六个平台发布包。')
    for item in manifest['artifacts']:
        path = release / item['file']
        if path.parent != release or path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
            raise SystemExit('发布包校验失败：' + item['file'])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    roots = {'portalapp', 'deploy', 'scripts', 'terminal-downloads'}
    files = {'README.md', 'requirements.txt', 'run.py', 'bootstrap.py'}
    blocked = {'__pycache__', '.pytest_cache', 'var', 'venv', '.venv'}
    included = []
    with zipfile.ZipFile(args.output, 'w', zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(PORTAL.rglob('*')):
            rel = path.relative_to(PORTAL)
            if not path.is_file() or path.is_symlink() or any(part in blocked or part.startswith('.venv') for part in rel.parts):
                continue
            if rel.parts[0] not in roots and str(rel) not in files:
                continue
            if path.suffix in {'.pyc', '.pyo', '.db', '.log'} or path.name in {'secret', 'users.passwd'}:
                continue
            bundle.write(path, 'cluster-portal/' + rel.as_posix())
            included.append(rel.as_posix())
    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    args.output.with_suffix('.zip.sha256').write_text(digest + '  ' + args.output.name + '\n')
    print(json.dumps(dict(package=str(args.output), files=len(included), sha256=digest, bytes=args.output.stat().st_size)))


if __name__ == '__main__':
    main()
