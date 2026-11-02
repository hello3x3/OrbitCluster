#!/usr/bin/env bash
# Native OrbitCluster CLI bootstrap. No Python, pip, Git or sudo.
set -euo pipefail
usage() { cat <<'HELP'
OrbitCluster CLI 安装器（macOS / Linux / WSL）
用法：bash install.sh --server http://集群地址 [--install-dir DIR] [--bin-dir DIR] [--config-dir DIR] [--ca-bundle FILE]
默认支持 HTTP；--require-https 可明确要求 HTTPS。Windows 原生请使用 install.ps1。
安装包仅从指定门户下载；安装过程不访问 GitHub 或 Python 包索引。
HELP
}
server=; install_dir=; bin_dir=; config_dir=; ca=; strict=0
while [ "$#" -gt 0 ]; do
 case "$1" in
  --help|-h) usage; exit 0;;
  --require-https) strict=1; shift;;
  --allow-http) strict=0; shift;;
  --server|--install-dir|--bin-dir|--config-dir|--ca-bundle)
   [ "$#" -ge 2 ] && [ -n "$2" ] || { printf '缺少参数值：%s\n' "$1" >&2; exit 2; }
   case "$1" in --server) server=$2;; --install-dir) install_dir=$2;; --bin-dir) bin_dir=$2;; --config-dir) config_dir=$2;; --ca-bundle) ca=$2;; esac; shift 2;;
  *) printf '未知参数：%s\n' "$1" >&2; exit 2;;
 esac
done
case "$server" in http://*|https://*) ;; *) printf '请提供完整 HTTP / HTTPS 服务地址。\n' >&2; exit 2;; esac
case "$server" in *\?*|*\#*|*\@*|*[[:space:]]*|*\\*) printf '服务地址格式无效。\n' >&2; exit 2;; esac
if [ "$strict" = 1 ] && [[ "$server" != https://* ]]; then printf '当前安装要求 HTTPS。\n' >&2; exit 2; fi
server=${server%/}
printf '[1/5] 检测操作系统和处理器\n'
case "$(uname -s)" in Darwin) platform=darwin;; Linux) platform=linux;; *) printf '此脚本支持 macOS、Linux 和 WSL；Windows 原生请使用 PowerShell 安装器。\n' >&2; exit 2;; esac
case "$(uname -m)" in arm64|aarch64) arch=arm64;; x86_64|amd64) arch=amd64;; *) printf '暂未提供当前处理器的安装包。\n' >&2; exit 2;; esac
for tool in curl tar mktemp sed; do command -v "$tool" >/dev/null || { printf '缺少系统工具：%s\n' "$tool" >&2; exit 2; }; done
work=$(mktemp -d); trap 'rm -rf "$work"' EXIT
fetch=(curl --fail --show-error --progress-bar --connect-timeout 15 --max-time 300 --max-redirs 0)
if [ -n "$ca" ] && [[ "$server" == https://* ]]; then fetch+=(--cacert "$ca"); fi
"${fetch[@]}" "$server/terminal/manifest.json" -o "$work/manifest.json"
version=$(sed -nE 's/.*"version"[[:space:]]*:[[:space:]]*"([0-9]+\.[0-9]+\.[0-9]+)".*/\1/p' "$work/manifest.json")
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { printf '门户未提供有效的 CLI 版本，原安装未修改。\n' >&2; exit 1; }
artifact="orbit-$version-$platform-$arch.tar.gz"
printf '[2/5] 从门户下载 %s\n' "$artifact"
"${fetch[@]}" "$server/terminal/downloads/$artifact" -o "$work/client.tar.gz"
"${fetch[@]}" "$server/terminal/downloads/$artifact.sha256" -o "$work/digest"
printf '[3/5] 校验下载文件\n'
read -r expected _ < "$work/digest"
[[ "$expected" =~ ^[a-fA-F0-9]{64}$ ]] || { printf '校验文件无效，安装未执行。\n' >&2; exit 1; }
if command -v shasum >/dev/null; then actual=$(shasum -a 256 "$work/client.tar.gz"); elif command -v sha256sum >/dev/null; then actual=$(sha256sum "$work/client.tar.gz"); else printf '系统缺少 SHA256 校验工具。\n' >&2; exit 2; fi
[ "${actual%% *}" = "$expected" ] || { printf 'SHA256 校验失败，原安装保持不变。\n' >&2; exit 1; }
# Only one regular executable is accepted. No archive path/link may escape work/.
[ "$(tar -tzf "$work/client.tar.gz")" = orbit ] && [[ "$(tar -tvzf "$work/client.tar.gz")" == -* ]] || { printf '安装包内容不正确。\n' >&2; exit 1; }
printf '[4/5] 解压并检查客户端\n'
tar -xzf "$work/client.tar.gz" -C "$work"
chmod 755 "$work/orbit"
[ "$("$work/orbit" --version)" = "orbit $version" ] || { printf '客户端版本检查失败。\n' >&2; exit 1; }
args=(setup --server "$server")
[ -z "$install_dir" ] || args+=(--install-dir "$install_dir")
[ -z "$bin_dir" ] || args+=(--bin-dir "$bin_dir")
[ -z "$config_dir" ] || args+=(--config-dir "$config_dir")
[ -z "$ca" ] || args+=(--ca-bundle "$ca")
[ "$strict" = 0 ] || args+=(--require-https)
printf '[5/5] 安装到当前用户目录并保存服务地址\n'
"$work/orbit" "${args[@]}"
printf '\n安装完成。运行 orbit login --username 你的用户名，然后 orbit instances list。\n'
printf '检查更新：orbit update --check；更新：orbit update。\n'
printf '若命令不在 PATH，请使用指定 bin-dir 下的 orbit（默认 ~/.local/bin/orbit）。\n'
printf '卸载：orbit uninstall；清理登录和配置：orbit uninstall --purge。\n'
