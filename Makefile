# ============================================================================
# OrbitCluster —— 一条 make 完成「变量替换」
#
#   make                                用 provision/cluster.conf 渲染到 provision/out/
#   make CONF=... OUT=...               换配置文件 / 输出目录
#   make SET="SSH_PORT=2222 USER=alice" 临时覆盖个别配置项（不写回 cluster.conf）
#   make print                          只打印到屏幕，不落盘
#   make sites                          渲染两套回归夹具（与真机 / 站点档案对照用）
#   make check                          渲染后打印「需要人工核对」清单
#   make test                           跑门户本地测试（CTL 安全 + 冒烟）
#   make clean                          清掉生成物
#   make help                           显示本帮助
#
# 只生成文件，不 ssh 任何机器、不改任何系统状态。
# 生成的 out/ 目录结构与目标机路径一一对应（见 provision/out/MANIFEST.md）。
# ============================================================================

SHELL       := bash
.SHELLFLAGS := -eu -o pipefail -c

CONF    ?= provision/cluster.conf
EXAMPLE := provision/cluster.conf.example
OUT     ?= provision/out
RENDER  := provision/render.sh
SET     ?=

# make SET="A=1 B=2" → -s 'A=1' -s 'B=2'
SET_ARGS  := $(foreach kv,$(SET),-s '$(kv)')
BASE_ARGS := -c $(CONF) -o $(OUT) $(SET_ARGS)

.DEFAULT_GOAL := render
.PHONY: render print sites check test clean help _need_conf

# ---- 默认目标：渲染 ----------------------------------------------------------
render: _need_conf
	@$(RENDER) $(BASE_ARGS) --clean

# ---- 只打印，不落盘（先看效果）------------------------------------------------
print: _need_conf
	@$(RENDER) $(BASE_ARGS) --print

# ---- 回归夹具：两套真实集群的参数，用来验证生成器 ----------------------------
sites:
	@$(RENDER) -c provision/conf/field-3node.conf -o provision/out-field --clean
	@$(RENDER) -c provision/conf/3090-2node.conf  -o provision/out-3090  --clean

# ---- 渲染后把「需要人工核对」的清单打出来 -------------------------------------
check: render
	@echo
	@echo "==> 需要人工核对（详见 $(OUT)/docs/_REPLACEMENT-REPORT.md）"
	@sed -n '/^## 渲染后仍保留的小写/,/^>/p' $(OUT)/docs/_REPLACEMENT-REPORT.md \
	   | grep '^- ' || true
	@echo
	@sed -n '/^## 未能替换的节点类占位符/,$$p' $(OUT)/docs/_REPLACEMENT-REPORT.md \
	   | grep '^- ' || true

# ---- 本地测试（不需要集群）---------------------------------------------------
test:
	@cd oa/cluster-portal && python3 tests/test_ctl_security.py
	@cd oa/cluster-portal && if [ -x .venv-test/bin/python ]; then \
	     .venv-test/bin/python tests/smoke_local.py; \
	   else \
	     echo "  (跳过 smoke：oa/cluster-portal/.venv-test 不存在，见 README 的测试一节)"; \
	   fi

# ---- 清理 --------------------------------------------------------------------
clean:
	@rm -rf provision/out provision/out-*
	@echo "==> 已清掉 provision/out*"

# ---- 首次执行：先造出 cluster.conf 再停下，要求改成自己的值 --------------------
_need_conf:
	@if [ ! -f $(CONF) ]; then \
	   cp -p $(EXAMPLE) $(CONF); \
	   echo "==> 已从样例生成 $(CONF)"; \
	   echo "    请先修改里面的节点名 / IP / 卡型 / 端口，然后重新执行 make"; \
	   exit 1; \
	 fi

help:
	@sed -n '4,15p' $(lastword $(MAKEFILE_LIST)) | sed 's/^# \{0,1\}//'
