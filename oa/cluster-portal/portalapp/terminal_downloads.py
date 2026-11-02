"""Public, read-only distribution of the native CLI. No account data here."""
import re
from pathlib import Path
from flask import abort, jsonify, send_file
from . import siteconf

CLIENT_VERSION = (Path(__file__).parent / 'static/terminal/VERSION').read_text().strip()
if not re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', CLIENT_VERSION):
    raise ValueError('CLI VERSION 必须为 major.minor.patch')
TARGETS = [('darwin', 'arm64'), ('darwin', 'amd64'), ('linux', 'amd64'), ('linux', 'arm64'), ('windows', 'amd64'), ('windows', 'arm64')]
ARTIFACTS = {f'orbit-{CLIENT_VERSION}-{system}-{arch}' + ('.zip' if system == 'windows' else '.tar.gz') for system, arch in TARGETS}
ALLOWED = ARTIFACTS | {name + '.sha256' for name in ARTIFACTS}


def download_root():
    return Path(siteconf.get('TERMINAL_DOWNLOAD_DIR', '') or Path(__file__).resolve().parent.parent / 'terminal-downloads')


def register_downloads(app):
    app.jinja_env.globals['terminal_cli_version'] = CLIENT_VERSION
    @app.get('/terminal/install.sh')
    def cli_install_shell():
        return send_file(Path(__file__).parent / 'static/terminal/install.sh', mimetype='text/plain', max_age=0)

    @app.get('/terminal/install.ps1')
    def cli_install_powershell():
        return send_file(Path(__file__).parent / 'static/terminal/install.ps1', mimetype='text/plain', max_age=0)

    @app.get('/terminal/skill.md')
    def cli_skill():
        return send_file(Path(__file__).parent / 'static/docs/orbitcluster-SKILL.md', mimetype='text/plain', max_age=0)

    @app.get('/terminal/manifest.json')
    def cli_manifest():
        root = download_root().resolve()
        artifacts = []
        for system, arch in TARGETS:
            name = f'orbit-{CLIENT_VERSION}-{system}-{arch}' + ('.zip' if system == 'windows' else '.tar.gz')
            target, digest = root / name, root / (name + '.sha256')
            item = dict(file=name, platform=system, arch=arch, available=False)
            try:
                if any(path.is_symlink() or not path.is_file() or path.resolve().parent != root for path in (target, digest)):
                    artifacts.append(item)
                    continue
                checksum = digest.read_text()[:1024].split()[0]
                if re.fullmatch(r'[a-fA-F0-9]{64}', checksum):
                    item.update(available=True, sha256=checksum.lower(), size=target.stat().st_size)
            except (OSError, UnicodeError, IndexError):
                pass
            artifacts.append(item)
        response = jsonify(version=CLIENT_VERSION, artifacts=artifacts)
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get('/terminal/downloads/<name>')
    def cli_download(name):
        if name not in ALLOWED:
            abort(404)
        root = download_root().resolve()
        target = root / name
        # Artifact symlinks cannot expose credentials/configuration outside the release directory.
        if target.is_symlink() or not target.is_file() or target.resolve().parent != root:
            return jsonify(ok=False, code='client_not_published', error='此平台的 CLI 安装包尚未发布，请联系管理员上传客户端发布包。'), 404
        response = send_file(target, as_attachment=True, download_name=name, max_age=0)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response
