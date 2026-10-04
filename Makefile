# ============================================================================
# OrbitCluster —— 一条 make 完成「变量替换」
#
#   make                                就地替换：文档 + 机器配置都写回**当前仓库**（默认）
#   make CONF=...                       换用别的 cluster.conf
#   make SET="SSH_PORT=2222 USER=alice" 临时覆盖个别配置项（不写回 cluster.conf）
#   make reset                          还原仓库模板（撤销就地替换）
#   make print                          只打印到屏幕，不落盘（先看效果）
#   make out                            不动仓库，另存到 provision/out/
#   make sites                          渲染两套回归夹具（验证生成器；可与真机逐项对照）
#   make check                          渲染后打印「需要人工核对」清单
#   make test                           跑门户本地测试（CTL 安全 + 冒烟）
#   make clean                          清掉 provision/out*
#   make help                           显示本帮助
#
# 就地替换会把仓库里的文档改写成本集群的实值版，**不要顺手 git commit**；
# 参数有变或要回到模板：make reset 后再 make。
# ============================================================================

SHELL       := bash
.SHELLFLAGS := -eu -o pipefail -c

CONF    ?= provision/cluster.conf
EXAMPLE := provision/cluster.conf.example
OUT     ?= provision/out
RENDER  := provision/render.sh
RESET   := provision/reset.sh
REPORT  := provision/REPLACEMENT-REPORT.md
SET     ?=

# make SET="A=1 B=2" → -s 'A=1' -s 'B=2'
SET_ARGS  := $(foreach kv,$(SET),-s '$(kv)')

.DEFAULT_GOAL := render
.PHONY: render out print reset sites check test clean help _need_conf

# ---- 默认目标：就地替换到当前仓库 --------------------------------------------
render: _need_conf
	@$(RENDER) -c $(CONF) $(SET_ARGS) --in-place

# ---- 不改仓库，另存到 provision/out/ -----------------------------------------
out: _need_conf
	@$(RENDER) -c $(CONF) -o $(OUT) $(SET_ARGS) --clean

# ---- 只打印，不落盘 ----------------------------------------------------------
print: _need_conf
	@$(RENDER) -c $(CONF) $(SET_ARGS) --print

# ---- 还原仓库模板 ------------------------------------------------------------
reset:
	@$(RESET)

# ---- 回归夹具：两套真实集群的参数，用来验证生成器 ----------------------------
sites:
	@$(RENDER) -c provision/conf/field-3node.conf -o provision/out-field --clean
	@$(RENDER) -c provision/conf/3090-2node.conf  -o provision/out-3090  --clean

# ---- 把「需要人工核对」的清单打出来（先 make，再 check）-----------------------
check:
	@if [ ! -f $(REPORT) ]; then \
	   echo "还没有渲染过 —— 先执行 make"; exit 1; \
	 fi
	@echo "==> 需要人工核对（详见 $(REPORT)）"
	@sed -n '/^## 渲染后仍保留的小写/,/^>/p' $(REPORT) | grep '^- ' || true
	@sed -n '/^## 未能替换的节点类占位符/,$$p' $(REPORT) | grep '^- ' || true

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
	@sed -n '4,19p' $(lastword $(MAKEFILE_LIST)) | sed 's/^# \{0,1\}//'
