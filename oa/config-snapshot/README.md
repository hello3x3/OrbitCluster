# config-snapshot —— 现场配置快照说明

这是“门户+集成配置”的**非密钥**快照，配合手册用于在另一台机器上复现；密钥类内容不落盘
（见下方“密码”一节）。

| 文件 | 内容 | 新机器怎么用 |
|---|---|---|
| `env-notes.md` | 主机/IP/端口/版本/路径/差异点总表 | 部署前先读，替换成你的值 |
| `nodes.txt` | 现场节点状态（含内存/gres） | 对照集群实际，确认容量匹配 |
| `images-list.txt` | /share/images 公共镜像清单 | 你的公共镜像名不同就替换（自动扫描，无需登记）；用户「保存镜像」的个人镜像在 `/share/images/<用户名>/` 下自动出现，无需登记 |
| `plans.json` | 当前 5 个套餐配置 | 安装后如需一致，用管理页手工录入或按 json 重建 |
| `users.json` | 平台账号（用户名/角色/os_mode/密钥数/端口），**不含密码**；其 quota 字段仅为名义值，实际配额以 OS repquota 为准 | 见 02-管理手册“用户/§1b”章节 |
| `settings.json` | portal_name / common_ports 等设置 | 默认即可；common_ports 可调 |
| `instances.json` | 历史资源实例导出（现场为空） | 参考结构，无需恢复 |
| `state.json` | 以上合并导出 | 兼容备份用 |
| `systemd/cluster-portal.service` | systemd 单元原文 | install.sh 会自动生成；这里作对照 |
| `systemd/portal.sudoers` | sudoers 白名单原文 | install.sh 自动写入；这里作对照 |
| `etc-cluster-portal/users.passwd.example` | 密码文件模板（含注释格式） | 复制成正式文件后填写/覆盖 |

## 密码（不在此目录存放明文）

所有门户账号明文密码位于旧机 `/etc/cluster-portal/users.passwd`。
复现账号密码的两种方式（任选）：
1. **直接整份复制**：`scp root@旧机:/etc/cluster-portal/users.passwd 新机:/etc/cluster-portal/users.passwd`，
   然后（root）`chown root:portal … && chmod 660 … && systemctl restart cluster-portal`；
2. 若不想沿用旧密码：安装后编辑该文件逐行写 `用户名:新密码`，约 20s 自动生效
   （或执行 `sync_once` 立即生效，见 02-管理手册 密码文件章节）。

## 校验

复制完成后建议：登录各账号、在“资源套餐”核对套餐、在“用户管理”核对用户与配额、
提交一个最小 CPU 资源验证全链路（步骤见 01-部署手册 验收一节）。
