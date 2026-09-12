# -*- coding: utf-8 -*-
"""站点配置 —— 让同一份门户代码适配不同集群（ssh 端口 / GPU 型号 / 套餐种子）。

优先级：**环境变量 > 站点配置文件 > 内置默认值**。

站点配置文件默认 `/etc/cluster-portal/site.conf`（可用 `PORTAL_SITE_CONF` 覆盖），
格式为每行 `KEY=VALUE`，`#` 起注释，键名大小写不敏感。识别的键：

  SSH_PORT            节点间 ssh 端口（特权助手连计算节点用）      默认 2180
  GPU_MODEL_MAP       额外的 gres->显示名映射，形如 "3060:RTX 3060,3090:RTX 3090"
  DEFAULT_GPU_MODEL   节点信息不可用时的兜底 GPU 型号              默认 RTX 3060
  ACCOUNT             Slurm 会计账户名（sacctmgr account）          默认 lab
  SEED_PLANS          首次建库时使用的套餐定义 JSON 路径（可选；缺省用内置 5 条）
  PARTITION           预留：覆盖分区名（默认按 sinfo 自动探测）

环境变量覆盖形式为 `PORTAL_<KEY>`，例如 `PORTAL_SSH_PORT=2022`。

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
    "ACCOUNT": "lab",
    "SEED_PLANS": "",
    "PARTITION": "",
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


CONF = dict(DEFAULTS)
CONF.update(_read_file(SITE_CONF))
for _k in list(DEFAULTS):                      # 环境变量优先级最高
    _env = os.environ.get("PORTAL_" + _k)
    if _env:
        CONF[_k] = _env


def get(key, default=None):
    k = key.upper()
    if CONF.get(k, "") != "":
        return CONF[k]
    if default is not None:
        return default
    return DEFAULTS.get(k, "")


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


GPU_MODEL_MAP = parse_gpu_map(CONF.get("GPU_MODEL_MAP", ""))
SSH_PORT = str(CONF.get("SSH_PORT", "2180"))
DEFAULT_GPU_MODEL = CONF.get("DEFAULT_GPU_MODEL", "RTX 3060")
SEED_PLANS = CONF.get("SEED_PLANS", "")
PARTITION = CONF.get("PARTITION", "")


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
