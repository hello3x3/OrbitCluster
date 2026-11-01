# ZJU-CV 功能测试报告（2026-11-01）

> 对象：3090-00 上的 OrbitCluster 门户 + Slurm 26.05.1 + enroot/pyxis + MariaDB/slurmdbd + /share 配额
> 方式：真实 HTTP 门户 + 真实 Slurm/容器/OS 操作；测试账号/用户均在结束后删除并校验无残留
> 脚本：[review/live_functional_20261101.py](live_functional_20261101.py)（需 `SUDO_PWD` 环境变量；root 门户密码从密码文件读取）

## 1. 结果

```text
通过 65 / 65
```

测试结束后检查：

- 无 `qa*` OS 用户、无 `/share/home/qa*`；
- 无 `qa*` Slurm account/QoS、无关联；
- 无遗留运行/排队测试作业；
- 门户 DB 只剩 `phd/ms/be/ra` 四个站点账户；
- `/images`、`shujiuhe`、`zju`、`root` 未被测试删除。

## 2. 覆盖项

### 2.1 登录与权限

- [x] root 登录；root 用户管理 / 资源套餐 / 管理账户页均为 200；
- [x] 默认账户 phd 删除保护；
- [x] 非 root 管理员登录；
- [x] 非 root 管理员访问 `/admin/accounts`、`/admin/plans` 均为 403；
- [x] 非 root 管理员可访问用户管理；
- [x] 非 root 管理员创建管理员被拒（只有 root 可以）。

### 2.2 账户

- [x] 创建 qalow（priority=1）、qahigh（priority=10）、qacap（cap=1）、qaexp（过期）；
- [x] 管理账户页显示新建账户；
- [x] QoS 实际值：qalow=1、qahigh=10、qacap `MaxTRESPU=gres/gpu=1`；
- [x] qahigh 勾选“可抢占”后，QoS Preempt 包含 acct-qalow；
- [x] qaexp 加入成员后，申请提交被“已过期”拒绝；
- [x] 用户提交不属于自己的账户（be）被拒；
- [x] 测试账户结束后全部删除。

### 2.3 用户/配额

- [x] root 创建普通用户（自动建号 + 多个账户 + 默认 qalow）；
- [x] 四台 OS 用户 UID 一致、家目录存在；
- [x] 门户 `/api/my/quota` OS 实读 100G；
- [x] 用户多账户可见、默认账户正确；
- [x] root 改配额 1G → 用户读到 1G → 写 1.1G 被配额拦截 → 恢复 100G；
- [x] root 删除普通用户：OS 账号、`/share/home`、Slurm 关联全部删除；
- [x] 创建/删除纯门户管理员（os_mode=none）。

### 2.4 门户资源申请

- [x] 普通用户登录、未完善资料被重定向；
- [x] 添加 SSH 公钥、登记两个端口、常用/低端口拒绝（客户端约束）；
- [x] 申请页账户下拉包含多个账户；
- [x] 使用 qahigh 账户提交 GPU 容器作业；
- [x] 实例 RUNNING、实例记录 `account=qahigh`；
- [x] 日志 API、停机 API、删除实例记录；
- [x] 不属于的账户/过期账户提交拒绝。

### 2.5 Slurm 优先级/限额

- [x] 用 qalow 占满 3090-02 的 4 张 GPU；
- [x] qalow 4 卡与 qahigh 4 卡作业同时排队；
- [x] 释放占用后 **qahigh 先启动**，qalow 没有同时启动；
- [x] qacap 单用户上限=1，提交 2 卡作业被 QOS/limits 限制。

> 抢占已进一步做**真实 kill 测试**：见下方“附加专项测试”。

### 2.6 管理账户 500 修复验证

- [x] `/admin/accounts` HTTP 200，页面含 phd/ms/be/ra；
- [x] `/admin/users` 中 shujiuhe 显示 `24 KiB / 500 GiB`。

## 3. 测试中发现并修复的问题

1. **缺少 `AccountingStorageTRES=gres/gpu`**
   - 现象：新建账户时 QoS 同步报 `no TRES known by type gres/gpu`，账户创建回滚。
   - 修复：slurm.conf 增加 `AccountingStorageTRES=gres/gpu`；四台同步并重启。
   - 现在 `sacctmgr show tres` 有 `gres|gpu`，`MaxTRESPerUser` 可正常写入。
2. **门户沙箱不允许写 `/etc`**
   - 现象：通过门户建普通用户报 `groupadd: cannot lock /etc/group; try again later.`
   - 根因：`ProtectSystem=strict` + `ReadWritePaths` 只放开了 data/cluster-portal/share，没放开 `/etc`；root 助手继承此命名空间，`groupadd/useradd` 写不了账号库。
   - 修复：`ReadWritePaths=/var/lib/cluster-portal /etc /share`；重启门户。
   - 之后门户“自动建号”真实成功，四台 UID 一致。
3. **QoS 限额清除方式**
   - 修复：cap=0 且当前确有上限时，用 `MaxTRESPerUser=gres/gpu=-1` 清除；空值在 26.05 上不会清掉旧值。新账户/本来不限的 QoS 不再发送无意义的 MaxTRESPerUser。
4. **管理账户页 500（已单独修复）**
   - `admin_accounts.html` 权限 600 → portal 读不到；chmod 644 + 重启。

## 3.1 附加专项：抢占 + 账户单用户卡数上限（22/22）

脚本：[review/preempt_cap_test_20261101.py](preempt_cap_test_20261101.py)

- 创建 qalowp（priority=1）、qahighp（priority=10、preempt=1）、qacapp（cap=1）；
- 低优用户 qalowu 在 3090-03 占满 4 张 GPU（sleep）；
- 高优用户 qahighu 用 qahighp 提交 4 卡作业；
- 结果：高优作业 RUNNING，低优作业被抢占为 CANCELLED/PREEMPTED；
- qacapp 单用户上限=1：
  - 单作业申请 2 卡被 QOS 限制；
  - 两个 1 卡作业并发时，第一个 RUNNING，第二个 PENDING，原因 `QOSMaxGRESPerUser`；
- 测试结束清理，无 qa 用户/账户/作业残留。

## 4. 本轮未覆盖

- 保存镜像/到期自动保存（保存 9.1G 容器需要较长异步时间）；
- 监控 §6；
- 门户 root 密码轮换/密码文件同步；
- 重启后 fstab/挂载/缓存清理 timer 的持久性（仅在当前运行态验证）。

## 5. 回归

- `make test`：`CTL_SECURITY_OK` + `SMOKE_OK`；
- 四节点 `sinfo` 全 idle；
- 门户 `cluster-portal` active，`http://3090-00/` 可登录。
