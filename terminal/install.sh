#!/usr/bin/env bash
# User-level OrbitCluster installer. No sudo or shell startup changes.
set -euo pipefail

usage() {
  cat <<'EOF'
OrbitCluster 用户终端安装器（Python >=3.10；无需 sudo）
用法：bash install.sh [选项]
  --source PATH         安装本地 terminal/ 源码目录或 wheel
  --server URL          配置集群 HTTP / HTTPS 地址
  --allow-http          明确允许 HTTP（仅可信内网；会明文传输凭证）
  --ca-bundle FILE      配置管理员提供的内部 CA 证书
  --install-dir DIR     用户级安装目录（默认 ~/.local/share/orbitcluster）
  --bin-dir DIR         启动器目录（默认 ~/.local/bin）
  --python PATH         使用指定 Python >=3.10
  --repository URL      Git 仓库 URL（默认 OrbitCluster GitHub 仓库）
  --ref REF             远端分支、标签或提交（默认 codex/frontend-optimization）
  --help                显示帮助
环境变量：ORBIT_INSTALL_SOURCE / ORBIT_INSTALL_DIR / ORBIT_INSTALL_BIN_DIR /
ORBIT_INSTALL_PYTHON / ORBIT_INSTALL_REPOSITORY / ORBIT_INSTALL_REF。
pip 镜像沿用 PIP_INDEX_URL；客户端配置位置可用 ORBIT_CONFIG_DIR 覆盖。
EOF
}

source_path=${ORBIT_INSTALL_SOURCE:-}
install_dir=${ORBIT_INSTALL_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/orbitcluster}
bin_dir=${ORBIT_INSTALL_BIN_DIR:-$HOME/.local/bin}
python_bin=${ORBIT_INSTALL_PYTHON:-}
repository=${ORBIT_INSTALL_REPOSITORY:-https://github.com/hello3x3/OrbitCluster.git}
ref=${ORBIT_INSTALL_REF:-codex/frontend-optimization}
server=
ca_bundle=
allow_http=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --help|-h) usage; exit 0 ;;
    --allow-http) allow_http=1; shift ;;
    --source|--server|--ca-bundle|--install-dir|--bin-dir|--python|--repository|--ref)
      if [ "$#" -lt 2 ] || [ -z "$2" ]; then
        printf '缺少参数值：%s\n' "$1" >&2; exit 2
      fi
      case "$1" in
        --source) source_path=$2 ;;
        --server) server=$2 ;;
        --ca-bundle) ca_bundle=$2 ;;
        --install-dir) install_dir=$2 ;;
        --bin-dir) bin_dir=$2 ;;
        --python) python_bin=$2 ;;
        --repository) repository=$2 ;;
        --ref) ref=$2 ;;
      esac
      shift 2 ;;
    *) printf '未知参数：%s\n' "$1" >&2; exit 2 ;;
  esac
done

case "$server" in
  http://*) if [ "$allow_http" != 1 ]; then
    printf 'HTTP 地址需要 --allow-http；仅用于可信内网。\n' >&2; exit 2
  fi ;;
esac

if [ -z "$python_bin" ]; then
  for candidate in python3 python3.13 python3.12 python3.11 python3.10 python; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3,10))' 2>/dev/null; then
      python_bin=$(command -v "$candidate")
      break
    fi
  done
fi
if [ -z "$python_bin" ] || ! "$python_bin" -c 'import sys; sys.exit(sys.version_info < (3,10))'; then
  printf '请先安装 Python >=3.10，或使用 --python 指定路径。\n' >&2; exit 1
fi

# Running a checked-out installer defaults to its matching local source.
installer_file=${BASH_SOURCE[0]:-}
if [ -z "$source_path" ] && [ -f "$installer_file" ]; then
  installer_dir=$(cd "$(dirname "$installer_file")" && pwd)
  if [ -f "$installer_dir/pyproject.toml" ]; then source_path=$installer_dir; fi
fi
if [ -z "$source_path" ]; then
  case "$repository" in git+*) ;; *) repository="git+$repository" ;; esac
  source_path="$repository@$ref#subdirectory=terminal"
fi
# pip treats a bare name such as `terminal` as an index requirement. Resolve
# existing paths before passing them across the installer/pip boundary.
if [ -d "$source_path" ]; then
  source_path=$(cd "$source_path" && pwd -P)
elif [ -f "$source_path" ]; then
  source_path="$(cd "$(dirname "$source_path")" && pwd -P)/$(basename "$source_path")"
fi

mkdir -p "$install_dir" "$bin_dir"
install_dir=$(cd "$install_dir" && pwd)
bin_dir=$(cd "$bin_dir" && pwd)
venv_dir="$install_dir/venv"
printf '正在安装 OrbitCluster 到 %s\n' "$install_dir"
if ! "$python_bin" -m venv "$venv_dir"; then
  printf '无法创建虚拟环境；Debian/Ubuntu 请先安装对应 python3-venv 包。\n' >&2
  exit 1
fi
"$venv_dir/bin/python" -m pip install --disable-pip-version-check --upgrade -- "$source_path"

# Generate shell quoting using Python: spaces, quotes and $ in paths stay literal.
"$python_bin" - "$venv_dir/bin/python" "$bin_dir/orbit" <<'PY'
import os, pathlib, shlex, sys, tempfile
executable, target = sys.argv[1], pathlib.Path(sys.argv[2])
marker = '# OrbitCluster user terminal launcher'
if target.is_symlink() or (target.exists() and marker not in target.read_text(errors='replace')[:150]):
    raise SystemExit('启动器路径已被其它程序占用，请用 --bin-dir 指定其它目录：' + str(target))
fd, temp = tempfile.mkstemp(prefix='.orbit-install-', dir=target.parent)
try:
    with os.fdopen(fd, 'w') as fh:
        fh.write('#!/bin/sh\n' + marker + '\nexec ' + shlex.quote(executable) + ' -m orbitcluster "$@"\n')
    os.chmod(temp, 0o755)
    os.replace(temp, target)
finally:
    if os.path.exists(temp): os.unlink(temp)
PY

if [ "$allow_http" = 1 ]; then
  printf '已允许 HTTP：密码和令牌将明文传输，请仅在可信内网使用。\n' >&2
  "$bin_dir/orbit" config set allow_http true >/dev/null
fi
if [ -n "$ca_bundle" ]; then "$bin_dir/orbit" config set ca_bundle "$ca_bundle" >/dev/null; fi
if [ -n "$server" ]; then "$bin_dir/orbit" config set server "$server" >/dev/null; fi
"$bin_dir/orbit" --version
printf '\n安装完成。运行：%s tui\n' "$bin_dir/orbit"
case ":${PATH:-}:" in
  *":$bin_dir:"*) printf '也可以直接运行：orbit tui\n' ;;
  *) printf '若要直接使用 orbit，请把 %s 加入 PATH。安装器未修改 shell 配置。\n' "$bin_dir" ;;
esac
if [ -z "$server" ]; then printf '首次启动可填写服务器地址，或执行 orbit config set server https://你的集群地址。\n'; fi
