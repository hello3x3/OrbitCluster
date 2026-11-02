/** Generate a local install command. This module never contacts a server. */
export function buildInstallCommand(server, caPath, ref, allowHttp = false) {
  const address = server.trim();
  if (!/^https?:\/\//i.test(address) || /[\s\x00-\x1f\x7f]/.test(address)) {
    throw new Error('请输入管理员提供的完整 HTTP 或 HTTPS 地址');
  }
  let url;
  try { url = new URL(address); } catch { throw new Error('服务地址格式不正确'); }
  if (!url.hostname || url.username || url.password || /[?#]/.test(address)) {
    throw new Error('服务地址不能包含用户名、密码、查询参数或片段');
  }
  const isHttp = url.protocol === 'http:';
  if (isHttp && !allowHttp) throw new Error('使用 HTTP 地址需先勾选“允许 HTTP”');
  if (!/^[a-f0-9]{40}$/.test(ref)) throw new Error('安装版本未正确配置，请联系管理员');
  const ca = caPath.trim();
  if (/[\x00-\x1f\x7f]/.test(ca)) throw new Error('证书路径不能包含换行或控制字符');
  if (isHttp && ca) throw new Error('CA 公共证书只用于 HTTPS 连接');
  const quote = value => "'" + value.replaceAll("'", "'\"'\"'") + "'";
  const normalized = url.href.replace(/\/+$/, '');
  const source = `https://raw.githubusercontent.com/hello3x3/OrbitCluster/${ref}/terminal/install.sh`;
  const caArgument = ca ? ` --ca-bundle ${quote(ca)}` : '';
  const httpArgument = isHttp ? ' --allow-http' : '';
  return `(
  install_script="$(mktemp)" || exit 1
  trap 'rm -f "$install_script"' EXIT
  curl -fsSL ${quote(source)} -o "$install_script" &&
  bash "$install_script" --ref ${quote(ref)} \\
    --server ${quote(normalized)}${httpArgument}${caArgument}
)`;
}
