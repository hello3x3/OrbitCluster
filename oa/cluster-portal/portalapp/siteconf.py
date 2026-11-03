# -*- coding: utf-8 -*-
"""站点配置 —— 让同一份门户代码适配不同集群（ssh 端口 / GPU 型号 / 套餐种子）。

优先级：**站点配置文件 > 环境变量 > 内置默认值**。
生产配置以 `/etc/cluster-portal/site.conf` 为准；环境变量只用于引导、测试或临时兜底。

站点配置文件默认 `/etc/cluster-portal/site.conf`（可用 `PORTAL_SITE_CONF` 覆盖），
格式为每行 `KEY=VALUE`，`#` 起注释，键名大小写不敏感。识别的键：

  SSH_PORT            节点间 ssh 端口（特权助手连计算节点用）      默认 2180
  GPU_MODEL_MAP       额外的 gres->显示名映射，形如 "3060:RTX 3060,3090:RTX 3090"
  DEFAULT_GPU_MODEL   节点信息不可用时的兜底 GPU 型号              默认 RTX 3060
  ACCOUNTS            Slurm 会计账户列表（逗号分隔）                默认取 ACCOUNT
  DEFAULT_ACCOUNT     申请资源时的默认账户                          默认第一个账户
  ACCOUNT             兼容旧单账户配置：ACCOUNTS 缺省时作为唯一账户  默认 lab
  SEED_PLANS          首次建库时使用的套餐定义 JSON 路径（可选；缺省用内置 5 条）
  IMAGES_MOUNT        镜像目录（.sqsh 放这里，门户按 *.sqsh 扫描）   默认 /share/images
  IMAGE_MAX_GB        单个个人镜像大小上限（GiB）                    默认 20
  PARTITION           预留：覆盖分区名（默认按 sinfo 自动探测）

环境变量兜底形式为 `PORTAL_<KEY>`，例如 `PORTAL_SSH_PORT=2022`；同名的站点配置文件键优先。

运行参数（`PORTAL_DATA`、`PORTAL_CTL`、`PORTAL_PORT`、`PORTAL_PASSWD_FILE` 等）也可以直接写进本文件；生产环境以本文件为准，systemd 的环境变量只作兼容兜底。

节点名**不在此配置**：节点列表与分区名一律经 sinfo 运行时探测
（见 deploy/portal-ctl 的 compute_nodes()/partition_name()），
节点 IP 从 /etc/hosts 解析，因此加节点无需改代码。
"""
import os
import re

SITE_CONF = os.environ.get("PORTAL_SITE_CONF", "/etc/cluster-portal/site.conf")

DEFAULTS = {
    "SSH_PORT": "2180",
    "GPU_MODEL_MAP": "",
    "DEFAULT_GPU_MODEL": "RTX 3060",
    "ACCOUNTS": "",
    "DEFAULT_ACCOUNT": "",
    "ACCOUNT": "lab",
    "SEED_PLANS": "",
    "IMAGES_MOUNT": "/share/images",
    "IMAGE_MAX_GB": "20",
    # 到期自动保存：0=关闭（默认，直接停机）；1=先保存 auto_*.sqsh 再停机。
    "AUTO_SAVE_ON_EXPIRE": "0",
    "PARTITION": "",
    "TERMINAL_DOWNLOAD_DIR": "",
    "TERMINAL_PUBLIC_URL": "",
    "TERMINAL_TOKEN_DAYS": "7",
    "TERMINAL_REQUIRE_HTTPS": "0",
    "TERMINAL_LOGIN_MAX_ATTEMPTS": "5",
    "TERMINAL_LOGIN_IP_MAX_ATTEMPTS": "50",
    "TERMINAL_LOGIN_WINDOW_S": "300",
    "TRUSTED_PROXY": "",
}


def _read_file(path):
    cfg = {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for raw in fh:
                ln = raw.split("#", 1)[0].strip()
                if not ln or "=" not in ln:
                    continue
                k, v = ln.split("=", 1)
                k = k.strip().upper()
                if k:
                    cfg[k] = v.strip()
    except OSError:
        pass
    return cfg


SITE = _read_file(SITE_CONF)
CONF = dict(DEFAULTS)
CONF.update(SITE)


def has_site(key):
    """该键是否由 /etc 站点配置文件显式设置（用于区分默认值与真实配置）。"""
    return key.upper() in SITE


def get(key, default=None):
    """站点配置优先；环境变量 PORTAL_<KEY> 仅作兜底。"""
    k = key.upper()
    if CONF.get(k, "") != "":
        return CONF[k]
    _env = os.environ.get("PORTAL_" + k)
    if _env not in (None, ""):
        return _env
    if default is not None:
        return default
    return DEFAULTS.get(k, "")


def runtime(key, default=None):
    """运行参数：site.conf 优先，其次是同名环境变量，最后才是默认值。

    与 get() 的区别：key 本身就带 PORTAL_ 前缀时，环境变量按原名读取；
    这样 /etc 配置可以覆盖 systemd 里的 PORTAL_DATA/PORTAL_PORT 等。
    """
    k = key.upper()
    # 只有站点文件显式写出的键才算“配置优先”；内置默认值不能遮蔽环境变量/测试兜底。
    if SITE.get(k, "") != "":
        return SITE[k]
    if k.startswith("PORTAL_"):
        _env = os.environ.get(k)
    else:
        _env = os.environ.get("PORTAL_" + k)
    if _env not in (None, ""):
        return _env
    if default is not None:
        return default
    return ""


def parse_gpu_map(text):
    """'3060:RTX 3060,3090:RTX 3090' -> {'3060': 'RTX 3060', '3090': 'RTX 3090'}"""
    out = {}
    for item in (text or "").split(","):
        item = item.strip()
        if not item or ":" not in item:
            continue
        k, v = item.split(":", 1)
        k, v = k.strip(), v.strip()
        if k and v:
            out[k] = v
    return out


GPU_MODEL_MAP = parse_gpu_map(get("GPU_MODEL_MAP", ""))
SSH_PORT = str(get("SSH_PORT", "2180"))
DEFAULT_GPU_MODEL = get("DEFAULT_GPU_MODEL", "RTX 3060")
SEED_PLANS = get("SEED_PLANS", "")
PARTITION = get("PARTITION", "")


def parse_accounts(text):
    """'phd, ms ,be' -> ['phd', 'ms', 'be']（去空白/空项/重复，保留出现顺序）。"""
    out = []
    for item in (text or "").split(","):
        name = item.strip()
        if name and name not in out:
            out.append(name)
    return out


# ---- 多账户（契约第 5 节）----
# ACCOUNT 保留兼容旧单账户站点；ACCOUNTS 缺省时它就是唯一账户
# （ACCOUNT 也显式配成空时 ACCOUNTS=[]，DEFAULT_ACCOUNT=""）。
ACCOUNT = get("ACCOUNT", "lab") or ""
ACCOUNTS = parse_accounts(get("ACCOUNTS", ""))
if not ACCOUNTS and ACCOUNT:
    ACCOUNTS = [ACCOUNT]
# DEFAULT_ACCOUNT 缺省取第一个账户；所有配置都空时为空串。
DEFAULT_ACCOUNT = (get("DEFAULT_ACCOUNT", "") or "").strip()
if not DEFAULT_ACCOUNT and ACCOUNTS:
    DEFAULT_ACCOUNT = ACCOUNTS[0]


def pretty_gpu(token):
    """gres 型号 token -> 展示名。站点映射优先；纯数字 token 自动补 RTX 前缀。

    例：'3090' -> 'RTX 3090'；'a100' -> 'a100'（除非站点显式映射）。
    """
    if not token:
        return token
    if token in GPU_MODEL_MAP:
        return GPU_MODEL_MAP[token]
    if re.fullmatch(r"\d{3,4}", token):
        return "RTX " + token
    return token
