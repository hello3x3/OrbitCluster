"""Public, read-only distribution of the native CLI. No account data here."""
import os
from pathlib import Path
from flask import abort, jsonify, send_file
from . import siteconf

CLIENT_VERSION = '0.2.0'
TARGETS = [('darwin', 'arm64'), ('darwin', 'amd64'), ('linux', 'amd64'), ('linux', 'arm64'), ('windows', 'amd64'), ('windows', 'arm64')]
ARTIFACTS = {f'orbit-{CLIENT_VERSION}-{system}-{arch}' + ('.zip' if system == 'windows' else '.tar.gz') for system, arch in TARGETS}
ALLOWED = ARTIFACTS | {name + '.sha256' for name in ARTIFACTS}


def download_root():
    return Path(siteconf.get('TERMINAL_DOWNLOAD_DIR', '') or Path(__file__).resolve().parent.parent / 'terminal-downloads')


def register_downloads(app):
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
        root = download_root()
        return jsonify(version=CLIENT_VERSION, artifacts=[dict(file=name, available=(root / name).is_file() and (root / (name + '.sha256')).is_file()) for name in sorted(ARTIFACTS)])

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
