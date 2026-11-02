#!/usr/bin/env python3
"""Bundle existing repository documents for the self-contained portal deployment."""
from pathlib import Path
import shutil


def main():
    root = Path(__file__).resolve().parents[3]
    output = root / 'oa/cluster-portal/portalapp/static/docs'
    output.mkdir(parents=True, exist_ok=True)
    installers = root / 'oa/cluster-portal/portalapp/static/terminal'
    installers.mkdir(parents=True, exist_ok=True)
    for name in ['install.sh', 'install.ps1']:
        shutil.copyfile(root / 'terminal' / name, installers / name)
    for source, name in [(root / 'architecture/index.html', 'architecture.html'),
                         (root / 'skills/orbitcluster/SKILL.md', 'orbitcluster-SKILL.md')]:
        shutil.copyfile(source, output / name)
        print('Bundled', source.relative_to(root), '->', name)


if __name__ == '__main__':
    main()
