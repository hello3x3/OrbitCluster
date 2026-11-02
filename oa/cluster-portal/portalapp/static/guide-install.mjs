/** Pure command generation. Downloads come only from the configured portal. */
export function buildInstallCommand(server, caPath = '', platform = 'unix') {
  const address = server.trim();
  if (!/^https?:\/\//i.test(address) || /[\s\x00-\x1f\x7f\\]/.test(address)) throw new Error('请输入完整 HTTP 或 HTTPS 服务地址');
  let url; try { url = new URL(address); } catch { throw new Error('服务地址格式不正确'); }
  if (!url.hostname || url.username || url.password || /[?#]/.test(address)) throw new Error('服务地址不能包含账号、密码、查询参数或片段');
  const ca = caPath.trim();
  if (/[\x00-\x1f\x7f]/.test(ca)) throw new Error('证书路径不能包含换行或控制字符');
  const base = url.href.replace(/\/+$/, '');
  if (url.protocol === 'http:' && ca) throw new Error('CA 公共证书只用于 HTTPS');
  if (platform === 'windows') {
    const quote = s => "'" + s.replaceAll("'", "''") + "'";
    const certificate = ca ? ` --cacert ${quote(ca)}` : '';
    const caArgument = ca ? ` -CaBundle ${quote(ca)}` : '';
    return `$installScript = Join-Path ([IO.Path]::GetTempPath()) ('orbit-' + [Guid]::NewGuid().ToString('N') + '.ps1')
try {
  curl.exe --fail --show-error --progress-bar${certificate} ${quote(base + '/terminal/install.ps1')} -o $installScript
  if ($LASTEXITCODE -ne 0) { throw '安装器下载失败' }
  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installScript -Server ${quote(base)}${caArgument}
  if ($LASTEXITCODE -ne 0) { throw '安装失败' }
} finally { Remove-Item -LiteralPath $installScript -Force -ErrorAction SilentlyContinue }`;
  }
  if (platform !== 'unix') throw new Error('请选择 macOS/Linux 或 Windows');
  const quote = s => "'" + s.replaceAll("'", "'\"'\"'") + "'";
  const certificate = ca ? ` --cacert ${quote(ca)}` : '';
  const caArgument = ca ? ` --ca-bundle ${quote(ca)}` : '';
  return `(
  install_script="$(mktemp)" || exit 1
  trap 'rm -f "$install_script"' EXIT
  curl --fail --show-error --progress-bar${certificate} ${quote(base + '/terminal/install.sh')} -o "$install_script" &&
  bash "$install_script" --server ${quote(base)}${caArgument}
)`;
}
