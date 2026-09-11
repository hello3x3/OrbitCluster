# -*- coding: utf-8 -*-
"""初始化/重置门户账号：python3 bootstrap.py <用户名> [密码] [role] [--force]

默认管理员为 root（平台最高管理员，通常由 install.sh 自动创建/重置）：
  python3 bootstrap.py root            # 自动生成随机密码
  python3 bootstrap.py root 新密码     # 指定密码
如需创建普通管理员（如无同名 OS 账号的纯平台管理账号）：
  python3 bootstrap.py <用户名> <密码> user|admin
密码缺省自动生成；--force 表示账号已存在时重置其密码。
需先设置 PORTAL_DATA 环境变量（默认 ./var）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    from portalapp.app import main_bootstrap
    args = sys.argv[1:]
    username = args[0]
    pwd = None
    role = "admin" if username == "root" else "user"
    force = "--force" in args
    rest = [a for a in args[1:] if a != "--force"]
    if rest:
        pwd = rest[0]
    if len(rest) > 1:
        role = rest[1]
    main_bootstrap(username, pwd, role, force=force)
