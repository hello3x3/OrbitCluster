/* OrbitCluster 集群门户前端脚本 */
"use strict";
(function () {
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));

  const ACTIVE_STATES = ["PENDING", "RUNNING", "SUSPENDED", "COMPLETING", "STOPPING"];
  // 个人镜像名（后端 portalapp/app.py 的 IMG_NAME_RE 同口径）：同公共镜像字符集，最长 32 位
  const IMG_NAME_RE_JS = /^[A-Za-z0-9_][A-Za-z0-9._+-]{0,31}$/;
  const IMG_NAME_HINT = "英文/数字/点/横线/下划线/加号，1-32 位，不能以点/横线/加号开头";
  const PUBLIC_IMG_NAME_RE_JS = /^[A-Za-z0-9_][A-Za-z0-9._+-]{0,127}$/;

  function csrfToken() {
    const m = $('meta[name="csrf-token"]');
    return m ? m.getAttribute("content") : "";
  }

  function toast(msg, type) {
    let t = $("#toast");
    if (!t) {
      t = document.createElement("div");
      t.id = "toast";
      t.className = "toast";
      document.body.appendChild(t);
    }
    t.textContent = msg;
    t.className = "toast " + (type || "ok");
    t.hidden = false;
    clearTimeout(t._h);
    t._h = setTimeout(() => (t.hidden = true), type === "err" ? 6000 : 3200);
  }

  /* ---------- 站内输入 / 确认弹窗（替代 window.prompt / window.confirm） ----------
     askText({title, label, value, hint, counter, validate, okText, danger})  → Promise<string|null>
     askConfirm({title, text, okText, danger})                                → Promise<true|null>
     - 焦点自动落在输入框/确定键；Enter 提交、Esc 或点遮罩取消；
     - validate 返回非空字符串时在弹窗里显示红字，而不是弹 toast。 */
  let dlgResolve = null;
  let dlgOpts = null;

  function dlgEls() {
    return { mask: $("#dlg-mask"), title: $("#dlg-title"), text: $("#dlg-text"),
             field: $("#dlg-field"), label: $("#dlg-label"), input: $("#dlg-input"),
             hint: $("#dlg-hint"), count: $("#dlg-count"), err: $("#dlg-err"), ok: $("#dlg-ok") };
  }

  function dlgClose(value) {
    const d = dlgEls();
    if (!d.mask || d.mask.hidden) return;
    d.mask.hidden = true;
    const cb = dlgResolve;
    dlgResolve = null;
    dlgOpts = null;
    if (cb) cb(value);
  }

  function dlgOpen(opts) {
    return new Promise(resolve => {
      const d = dlgEls();
      if (!d.mask) {                       // 兜底：页面没有弹窗标记时退回浏览器原生控件
        if (opts.kind === "confirm") resolve(window.confirm(opts.text || "确定？") ? true : null);
        else resolve(window.prompt(opts.text || opts.label || "", opts.value || ""));
        return;
      }
      const isAsk = opts.kind !== "confirm";
      dlgResolve = resolve;
      dlgOpts = opts;
      d.title.textContent = opts.title || (isAsk ? "输入" : "确认");
      d.text.hidden = isAsk;
      d.text.textContent = opts.text || "";
      d.field.hidden = !isAsk;
      d.label.textContent = opts.label || "";
      d.label.hidden = !opts.label;
      d.input.value = isAsk ? (opts.value || "") : "";
      d.hint.textContent = opts.hint || "";
      d.err.hidden = true;
      d.ok.textContent = opts.okText || "确定";
      d.ok.className = "btn btn-sm " + (opts.danger ? "btn-danger" : "btn-primary");
      d.input.oninput = () => dlgSyncCount();
      dlgSyncCount();
      d.mask.hidden = false;
      setTimeout(() => { if (isAsk) { d.input.focus(); d.input.select(); } else d.ok.focus(); }, 0);
    });
  }

  function dlgSyncCount() {
    const d = dlgEls();
    if (!d.count) return;
    if (dlgOpts && dlgOpts.counter) {
      d.count.hidden = false;
      d.count.textContent = dlgOpts.counter(d.input.value);
    } else {
      d.count.hidden = true;
    }
  }

  function dlgSubmit() {
    const d = dlgEls();
    const o = dlgOpts || {};
    if (o.kind === "confirm") { dlgClose(true); return; }
    const val = d.input.value;
    const err = o.validate ? o.validate(val) : "";
    if (err) {
      d.err.textContent = err;
      d.err.hidden = false;
      d.input.focus();
      return;
    }
    dlgClose(val);
  }

  function askText(opts) { return dlgOpen(Object.assign({ kind: "ask" }, opts || {})); }
  function askConfirm(opts) { return dlgOpen(Object.assign({ kind: "confirm" }, opts || {})); }

  function wireDialog() {
    const d = dlgEls();
    if (!d.mask) return;
    $$("[data-dlg-cancel]", d.mask).forEach(b => b.addEventListener("click", () => dlgClose(null)));
    d.ok.addEventListener("click", dlgSubmit);
    d.mask.addEventListener("click", ev => { if (ev.target === d.mask) dlgClose(null); });
    d.input.addEventListener("keydown", ev => {
      if (ev.key === "Enter") { ev.preventDefault(); dlgSubmit(); }
      if (ev.key === "Escape") { ev.preventDefault(); dlgClose(null); }
    });
    document.addEventListener("keydown", ev => {
      if (d.mask.hidden) return;
      if (ev.key === "Escape") { ev.preventDefault(); dlgClose(null); }
      if (ev.key === "Enter" && ev.target !== d.input && !d.field.hidden) {
        ev.preventDefault(); dlgSubmit();
      }
    });
  }

  async function post(url, body) {
    const opt = { method: "POST", headers: { "X-CSRF-Token": csrfToken() } };
    if (body instanceof FormData) opt.body = body;
    else if (body) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
    const r = await fetch(url, opt);
    let data = null;
    try { data = await r.json(); } catch (e) { /* non-json */ }
    if (!r.ok && !data) throw new Error("HTTP " + r.status);
    return data;
  }

  async function get(url) {
    const r = await fetch(url, { headers: { "X-CSRF-Token": csrfToken() } });
    return r.json();
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g,
      c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  /* ---------- 通用：xform / xact / 弹窗 ---------- */
  function wireGeneric() {
    $$(".xform").forEach(f => {
      f.addEventListener("submit", async ev => {
        ev.preventDefault();
        const btn = f.querySelector("[type=submit]");
        if (btn && btn.disabled) return;   // 校验未通过时即使回车/脚本也不放行
        if (btn) btn.disabled = true;
        try {
          const d = await post(f.dataset.url, new FormData(f));
          if (d.ok) {
            toast(d.msg || "成功", "ok");
            setTimeout(() => location.reload(), 700);
          } else toast(d.error || "失败", "err");
        } catch (e) { toast(e.message || "网络错误", "err"); }
        if (btn) btn.disabled = false;
      });
    });

    $$(".xact, .xdel").forEach(b => {
      b.addEventListener("click", async () => {
        const msg = b.dataset.msg || "确定执行该操作？";
        const go = await askConfirm({
          title: b.dataset.title || (b.textContent || "").trim() || "确认操作",
          text: msg, okText: b.dataset.okText || "确定",
          danger: b.classList.contains("xdel"),
        });
        if (!go) return;
        try {
          const d = await post(b.dataset.url, new FormData());
          if (d.ok) {
            if (d.password) {
              toast(d.username + " 的新门户密码：\n" + d.password + "\n（仅本次显示）", "ok");
              setTimeout(() => location.reload(), 6000);
            } else { toast(d.msg || "成功", "ok"); setTimeout(() => location.reload(), 600); }
          } else toast(d.error || "失败", "err");
        } catch (e) { toast(e.message, "err"); }
      });
    });

    $$("[data-close]").forEach(b => {
      b.addEventListener("click", () => { $("#" + b.dataset.close).hidden = true; });
    });
    $$(".modal-mask").forEach(m => {
      if (m.id === "dlg-mask") return;      // 通用弹窗自己管（关闭时要 resolve Promise）
      m.addEventListener("click", ev => { if (ev.target === m) m.hidden = true; });
    });
    $$("#btn-refresh").forEach(b => b.addEventListener("click", () => location.reload()));

    /* 顶部提示：固定浮层，点 ✕ 立即收起；错误留 9s、其它 6s 后自动淡出（不占正文流） */
    $$(".flash").forEach(f => {
      let gone = false;
      const close = () => {
        if (gone) return;
        gone = true;
        f.classList.add("flash-hide");
        setTimeout(() => f.remove(), 260);
      };
      const x = f.querySelector("[data-flash-close]");
      if (x) x.addEventListener("click", close);
      setTimeout(close, f.classList.contains("flash-error") ? 9000 : 6000);
    });

    /* 管理员改配额：弹出输入框（例 100G/500G/1T）后提交 */
    $$(".act-quota").forEach(b => {
      b.addEventListener("click", async () => {
        const user = b.dataset.user || "";
        // 系统保留账号（root 等）只能填「不限」（= 清除限额）：它们的家目录不在 /share 上，
        // 设具体额度还可能威胁系统自身写入，后端也会拒绝。这里直接把提示与默认值换成「不限」。
        const reserved = b.dataset.reserved === "1";
        const msg = reserved
          ? ("为 " + user + " 设置 /share 配额。\n"
             + "这是系统保留账号：只能填「不限」（清除限额），填具体额度会被后端拒绝。")
          : ("为 " + user + " 设置 /share 磁盘配额（软=硬。例：100G / 500G / 1T；填「不限」表示不限额）：");
        const size = await askText({
          title: "设置 /share 磁盘配额",
          label: msg.split("\n")[0],
          value: reserved ? "不限" : "",
          placeholder: reserved ? "不限" : "例：100G / 500G / 1T",
          hint: reserved
            ? "系统保留账号只能填「不限」（清除限额），填具体额度会被后端拒绝。"
            : "软限=硬限，单位可用 G/T；填「不限」表示不限额。",
          okText: "保存配额",
          validate: v => v.trim() ? "" : "请填写配额，例如 100G / 500G / 1T / 不限",
        });
        if (size === null || !size.trim()) return;
        try {
          const fd = new FormData();
          fd.append("quota", size.trim().toUpperCase());
          const d = await post(b.dataset.url, fd);
          if (d.ok) { toast(d.msg || "配额已更新", "ok"); setTimeout(() => location.reload(), 900); }
          else toast(d.error || "设置失败", "err");
        } catch (e) { toast(e.message || "网络错误", "err"); }
      });
    });
    wireProfilePorts();
  }

  /* ---------- 个人资料：端口实时校验 ---------- */
  function wireProfilePorts() {
    const dataEl = $("#profile-data");
    const input = $("#port-num");
    const btn = $("#btn-port-add");
    const hint = $("#port-hint");
    if (!dataEl || !input || !btn || !hint) return;
    let data = { common_ports: [], mine: [], claimed: [] };
    try { data = JSON.parse(dataEl.textContent); } catch (e) { /* noop */ }
    const common = new Set(data.common_ports || []);
    const mine = new Set(data.mine || []);
    const claimed = new Set(data.claimed || []);
    const MIN = 10000, MAX = 65535;

    function validate() {
      const raw = input.value.trim();
      const reasons = [];
      if (raw === "") reasons.push("请输入端口号");
      else {
        const n = Number(raw);
        if (!Number.isInteger(n)) reasons.push("端口必须是整数");
        else {
          if (n < MIN) reasons.push("端口必须 ≥ " + MIN + "（" + MIN + " 以下多为系统/常用端口）");
          if (n > MAX) reasons.push("端口不能超过 " + MAX);
          if (n >= MIN && n <= MAX) {
            if (common.has(n)) reasons.push("端口 " + n + " 属于常用/保留端口，不能申请");
            if (mine.has(n)) reasons.push("端口 " + n + " 已在你的端口池中");
            else if (claimed.has(n)) reasons.push("端口 " + n + " 已被其他用户申请");
          }
        }
      }
      const valid = reasons.length === 0;
      hint.hidden = valid;
      hint.textContent = reasons.join("；");
      btn.disabled = !valid;
      return valid;
    }
    input.addEventListener("input", validate);
    input.addEventListener("change", validate);
    validate(); // 初始为空 → 禁用按钮
  }

  /* ---------- 我的资源页 ---------- */
  function rowHtml(i) {
    const terminal = !ACTIVE_STATES.includes(String(i.state || ""));
    let ops = "";
    if (i.state === "RUNNING") {
      ops += `<button class="btn btn-ghost btn-sm act-detail" data-id="${i.id}">连接/详情</button> `;
      if (i.saving_now)
        ops += `<button class="btn btn-ghost btn-sm" disabled title="正在导出容器 rootfs 为 .sqsh（分钟级）">保存中…</button> `;
      else
        ops += `<button class="btn btn-ghost btn-sm act-save" data-id="${i.id}">保存镜像</button> `;
    }
    ops += `<button class="btn btn-ghost btn-sm act-log" data-id="${i.id}">日志</button> `;
    if (i.can_stop && !i.saving_now)
      ops += `<button class="btn btn-danger btn-sm act-stop" data-id="${i.id}">停机</button>`;
    if (terminal) {
      ops += `<button class="btn btn-ghost btn-sm act-restart" data-id="${i.id}">重新启动</button> `;
      ops += `<button class="btn btn-danger btn-sm act-delinst" data-id="${i.id}">删除</button>`;
    }
    const subBits = [i.plan_name, i.image_name].filter(Boolean);
    const sub = subBits.join(" · ");
    const saveLine = i.last_save
      ? `<div class="small ${i.saving_now ? "" : (i.last_save.indexOf("失败") >= 0 || i.last_save.indexOf("异常") >= 0 ? "txt-err" : "muted")}">💾 ${esc(i.last_save)}</div>`
      : "";
    // 启动失败（例如端口被占用）的原因：日志里定性后就一直显示，别让它一闪而过
    const errLine = i.startup_error
      ? `<div class="small txt-err">⚠ ${esc(i.startup_error)}</div>`
      : "";
    return `<tr data-id="${i.id}">
      <td class="mono">${esc(i.job_id)}</td>
      <td><b>${esc(i.res_name)}</b>${sub ? `<div class="small muted">${esc(sub)}</div>` : ""}${saveLine}${errLine}</td>
      <td><span class="st st-${esc(i.state_key || String(i.state).toLowerCase())}">${esc(i.state_cn)}</span></td>
      <td class="mono">${esc(i.node_cn)}</td>
      <td class="mono">${esc(i.port)}</td>
      <td class="small">${i.gpus}×GPU · ${i.cpus}核 · ${i.mem_gb}G</td>
      <td class="mono small">${esc(i.hours)}</td>
      <td class="small">${esc(i.created)}</td>
      <td class="ops">${ops}</td></tr>`;
  }

  function wireMyPage() {
    if (!$("#inst-body")) return;
    let last = {};
    let pollMs = 0;
    // 轮询间隔按需要多快看到变化分档（见 load() 里的 armPoll 调用）。
    function armPoll(ms) {
      if (pollMs === ms) return;
      if (window._poll) clearInterval(window._poll);
      pollMs = ms;
      window._poll = ms ? setInterval(load, ms) : null;
    }
    async function load() {
      try {
        const list = await get("/api/my/instances");
        const body = $("#inst-body");
        const empty = $("#inst-empty");
        if (!list.length) {
          body.innerHTML = "";
          if (empty) empty.hidden = false;
          armPoll(60000);          // 没有实例：慢速轮询即可，但**不要停**
          return;
        }
        if (empty) empty.hidden = true;
        last = {};
        body.innerHTML = list.map(i => { last[i.id] = i; return rowHtml(i); }).join("");
        const hasActive = list.some(i => i.can_stop);
        // 分档：正在启动（几十秒的高关注窗口）→ 2.5s；排队/收尾 → 6s；
        // 稳态有实例在跑 → 15s；全都不活跃 → 60s（仍然轮询，不再停掉）。
        const starting = list.some(i => i.starting);
        const queued = list.some(i => i.state === "PENDING" || i.state === "COMPLETING");
        armPoll(!hasActive ? 60000 : (starting ? 2500 : (queued ? 6000 : 15000)));
      } catch (e) { /* 忽略瞬时错误，下一个周期再试 */ }
    }
    load();
    armPoll(15000);   // 先按稳态起步；load() 回来后会按实际情况（启动中/排队中）重新分档
    $("#inst-body").addEventListener("click", async ev => {
      const b = ev.target.closest("button");
      if (!b) return;
      const id = b.dataset.id;
      if (b.classList.contains("act-log")) {
        $("#log-modal").hidden = false;
        $("#log-title").textContent = "（作业 #" + id + "）";
        $("#log-body").textContent = "加载中…";
        $("#log-events").textContent = "加载中…";
        try {
          const d = await get("/instances/" + id + "/log?lines=300");
          if (!d.ok) {
            $("#log-body").textContent = "错误：" + d.error;
            $("#log-events").textContent = "—";
          } else {
            $("#log-body").textContent = d.log_error
              ? ("（读不到容器日志：" + d.log_error + "）")
              : (d.text || "（暂无输出）");
            const ev = d.events || [];
            $("#log-events").textContent = ev.length
              ? ev.map(e => `${e.ts}  [${e.label}] ${e.message}`).join("\n")
              : "（暂无门户操作记录）";
          }
        } catch (e) {
          $("#log-body").textContent = "读取失败：" + e.message;
          $("#log-events").textContent = "—";
        }
      } else if (b.classList.contains("act-detail")) {
        const i = last[id];
        if (!i) return;
        $("#detail-modal").hidden = false;
        $("#detail-title").textContent = "（作业 #" + i.job_id + "）";
        // SSH 连接直接用真实节点 IP（ssh_ip），避免给出不可解析的主机名
        const sshHost = (i.ssh_ip || i.node || "").trim() || "（等待分配）";
        const sshNode = `ssh -p ${i.port} ${esc(i.username)}@${sshHost}`;
        const nodeLine = i.node ? `<p class="muted small">运行节点：${esc(i.node)}${
          i.ssh_ip && i.ssh_ip !== i.node ? "（" + esc(i.ssh_ip) + "）" : ""}</p>` : "";
        $("#detail-body").innerHTML = `
          <h3>SSH 连接</h3>
          <div class="cmd-row">
            <pre class="cmd-preview" id="detail-ssh-cmd">${esc(sshNode)}</pre>
            <button class="btn btn-ghost btn-sm btn-copy" type="button"
                    data-copy-from="#detail-ssh-cmd" title="复制这条 SSH 命令">复制</button>
          </div>
          ${nodeLine}
          <p class="muted small">用你登记公钥对应的<b>私钥</b>连接；首次连接若提示 host key 变化，
          因容器 host key 存放在你的家目录（.ssh-hostkeys），更换机器/清理后需重新接受。</p>`;
      } else if (b.classList.contains("act-save")) {
        const rec = last[id] || {};
        let name = await askText({
          title: "保存为个人镜像",
          label: "镜像名（保存到 /share/images/" + (rec.username || "") + "/<名称>.sqsh）",
          value: rec.res_name ? String(rec.res_name).replace(/[^A-Za-z0-9_]/g, "_").slice(0, 32) : "",
          hint: IMG_NAME_HINT + "；导出计入你的 /share 配额",
          counter: v => v.length + " / 32",
          okText: "开始保存",
          validate: v => IMG_NAME_RE_JS.test(v.trim()) ? "" : "只能包含" + IMG_NAME_HINT,
        });
        if (name === null) return;
        name = name.trim();
        async function doSave(force) {
          const fd = new FormData();
          fd.append("name", name);
          if (force) fd.append("force", "1");
          const d = await post("/instances/" + id + "/save-image", fd);
          return d;
        }
        b.disabled = true;
        try {
          let d = await doSave(false);
          if (!d.ok && d.need_force) {
            const over = await askConfirm({
              title: "覆盖已有镜像？", text: (d.error || "同名镜像已存在") + "，是否覆盖？",
              okText: "覆盖", danger: true,
            });
            if (!over) { b.disabled = false; return; }
            d = await doSave(true);
          }
          toast(d.msg || (d.ok ? "已开始保存镜像" : d.error), d.ok ? "ok" : "err");
          if (d.ok) setTimeout(load, 1200);
        } catch (e) { toast(e.message || "网络错误", "err"); }
        b.disabled = false;
      } else if (b.classList.contains("act-stop")) {
        const goStop = await askConfirm({
          title: "停止资源", okText: "停止", danger: true,
          text: "确定停止该资源（作业 #" + (last[id] ? last[id].job_id : id) + "）？"
                + "\n运行中的容器会被终止，端口随即释放。",
        });
        if (!goStop) return;
        b.disabled = true;
        try {
          const d = await post("/instances/" + id + "/stop", new FormData());
          toast(d.msg || (d.ok ? "已停机" : d.error), d.ok ? "ok" : "err");
          if (d.ok) setTimeout(load, 1800);
        } catch (e) { toast(e.message, "err"); }
        b.disabled = false;
      } else if (b.classList.contains("act-restart")) {
        const rec = last[id] || {};
        const goRestart = await askConfirm({
          title: "重新启动资源", okText: "重新启动",
          text: "确定按原参数重新启动该资源？\n作业 #" + (rec.job_id || id)
                + " 将重新入队（镜像/套餐资源/端口/时长/任务名不变）。",
        });
        if (!goRestart) return;
        b.disabled = true;
        try {
          const d = await post("/instances/" + id + "/restart", new FormData());
          toast(d.msg || (d.ok ? "已重新启动" : d.error), d.ok ? "ok" : "err");
          if (d.ok) setTimeout(load, 1500);
        } catch (e) { toast(e.message, "err"); }
        b.disabled = false;
      } else if (b.classList.contains("act-delinst")) {
        const rec = last[id] || {};
        const goDelInst = await askConfirm({
          title: "删除资源记录", okText: "删除", danger: true,
          text: "确定删除这条已停止/结束的资源记录？\n作业 #" + (rec.job_id || id)
                + " 的记录与日志文件将被删除（不可恢复）。",
        });
        if (!goDelInst) return;
        b.disabled = true;
        try {
          const d = await post("/instances/" + id + "/delete", new FormData());
          toast(d.msg || (d.ok ? "已删除" : d.error), d.ok ? "ok" : "err");
          if (d.ok) setTimeout(load, 1500);
        } catch (e) { toast(e.message, "err"); }
        b.disabled = false;
      }
    });
  }

  /* ---------- 申请资源页（镜像+套餐表单）---------- */
  function maxtimeOf(f) {
    const r = f.querySelector('input[name="plan_id"]:checked');
    if (r && r.dataset.maxtime) return parseInt(r.dataset.maxtime, 10) || 0;
    const sel = f.querySelector('#plan-sel');
    const o = sel && sel.selectedOptions[0];
    return o && o.dataset ? (parseInt(o.dataset.maxtime, 10) || 0) : 0;
  }
  function checkTask(input, hint) {
    const v = (input.value || "").trim();
    const len = Array.from(v).length;
    let ok = true, msg = "";
    if (len === 0) { ok = false; msg = "任务名称必填（3-10 个字符）"; }
    else if (len < 3) { ok = false; msg = "任务名称至少 3 个字符（当前 " + len + "）"; }
    else if (len > 10) { ok = false; msg = "任务名称最多 10 个字符（当前 " + len + "）"; }
    if (!ok) { hint.textContent = msg; }
    hint.hidden = ok;
    return ok;
  }

  /* ---------- 普通用户申请页 ---------- */
  function wireApplyForm() {
    const f = $("#apply-form");
    if (!f) return;
    const task = f.querySelector("#task-name"), th = f.querySelector("#task-hint");
    const hoursEl = f.querySelector("#hours"), hint = f.querySelector("#hours-hint");
    const btn = f.querySelector("#btn-submit");
    const radios = Array.from(f.querySelectorAll('input[name="plan_id"]'));
    function overInfo() {
      const mx = maxtimeOf(f);
      const v = parseInt(hoursEl.value || "0", 10);
      if (mx) hoursEl.max = mx;
      if (mx && v > mx) {
        hint.hidden = false;
        hint.textContent = "所选套餐最长 " + mx + " 小时，当前 " + v + " 小时超出：已禁用提交，如需更长请平台管理员代为申请";
        return true;
      }
      hint.hidden = true;
      return false;
    }
    function upd() {
      const nameOk = checkTask(task, th);
      const over = overInfo();
      btn.disabled = !(nameOk && !over);
    }
    task.addEventListener("input", upd);
    hoursEl.addEventListener("input", upd);
    hoursEl.addEventListener("change", upd);
    radios.forEach(r => r.addEventListener("change", upd));
    upd();
    f.addEventListener("submit", async ev => {
      ev.preventDefault();
      if (!btn || btn.disabled) return;
      btn.disabled = true;
      try {
        const d = await post("/apply", new FormData(f));
        if (d.ok) {
          toast("提交成功！作业 #" + d.job_id + " 已进入队列", "ok");
          setTimeout(() => (location.href = "/my"), 900);
        } else { toast(d.error || "提交失败", "err"); btn.disabled = false; }
      } catch (e) { toast(e.message || "网络错误", "err"); btn.disabled = false; }
    });
  }

  /* ---------- 管理员代申请 ---------- */
  function wireAdminApply() {
    const f = $("#admin-apply-form");
    if (!f) return;
    const tsel = f.querySelector("#target-sel");
    const aport = f.querySelector("#aport");
    const aportHint = f.querySelector("#aport-hint");
    const ahours = f.querySelector("#ahours");
    const ahint = f.querySelector("#ahours-hint");
    const task = f.querySelector("#atask"), th = f.querySelector("#atask-hint");
    const planSel = f.querySelector("#plan-sel");
    const btn = f.querySelector("#btn-admin-submit");
    let targets = [];
    let portReady = false;
    let imgReady = false;
    function overInfoAdmin() {
      const mx = maxtimeOf(f);
      const v = parseInt(ahours.value || "0", 10);
      if (mx && v > mx) {
        ahint.hidden = false;
        ahint.textContent = "所选套餐默认上限 " + mx + " 小时，当前 " + v + " 小时已超出（管理员代申请不受限，可提交）";
      } else ahint.hidden = true;
    }
    function upd() {
      btn.disabled = !(portReady && imgReady && checkTask(task, th));
    }
    async function loadTargets() {
      try {
        targets = await get("/api/apply-targets");
        tsel.innerHTML = '<option value="">请选择用户…</option>' + targets.map(t =>
          `<option value="${t.id}">${esc(t.username)}${t.display_name ? "（" + esc(t.display_name) + "）" : ""}` +
          `【密钥 ${t.keys} · 端口 ${t.ports.length}】</option>`).join("");
        if (targets.length === 0) {
          tsel.innerHTML = '<option value="">（暂无可代申请的用户）</option>';
        }
      } catch (e) { /* ignore */ }
    }
    async function loadImages() {
      const uid = parseInt(tsel.value || "0", 10);
      const sel = f.querySelector("#aimg-sel");
      imgReady = false;
      upd();
      if (!uid) {
        sel.innerHTML = '<option value="">先选择用户…</option>';
        sel.disabled = true;
        return;
      }
      sel.disabled = true;
      sel.innerHTML = '<option value="">加载镜像中…</option>';
      try {
        const d = await get("/api/user-images/" + uid);
        if (!d || (!d.public && !d.mine)) throw new Error("无镜像");
        const pub = (d.public || []).map(x =>
          `<option value="${esc(x.path)}">${esc(x.name)}</option>`).join("");
        const mine = (d.mine || []).map(x =>
          `<option value="${esc(x.path)}">${esc(x.name)}（我的）</option>`).join("");
        sel.innerHTML = (pub ? `<optgroup label="公共镜像（${d.public.length}）">${pub}</optgroup>` : "")
          + (mine ? `<optgroup label="${esc((targets.find(t => t.id === uid) || {}).username)} 的个人镜像（${d.mine.length}）">${mine}</optgroup>` : "");
        imgReady = !!(pub || mine);
        sel.disabled = !(pub || mine);
        if (!(pub || mine)) sel.innerHTML = '<option value="">（无可申请镜像）</option>';
      } catch (e) {
        sel.innerHTML = '<option value="">镜像加载失败</option>';
      }
      upd();
    }
    function refreshPorts() {
      const uid = parseInt(tsel.value || "0", 10);
      const t = targets.find(x => x.id === uid);
      aport.innerHTML = "";
      if (!t) {
        aportHint.hidden = false; aportHint.textContent = "先选择用户，使用该用户登记（且未被占用）的端口";
        portReady = false;
      } else if (t.ports.length === 0) {
        aportHint.hidden = false; aportHint.textContent = "该用户端口池为空，需先在门户登记端口";
        portReady = false;
      } else {
        aportHint.hidden = true;
        aport.innerHTML = t.ports.map(p =>
          `<option value="${p.port}" ${p.busy ? "disabled" : ""}>${p.port}${p.busy ? "（使用中）" : ""}</option>`).join("");
        portReady = t.ports.some(p => !p.busy);
      }
      loadImages();
      upd();
    }
    tsel.addEventListener("change", refreshPorts);
    aport.addEventListener("change", upd);
    planSel.addEventListener("change", () => { overInfoAdmin(); upd(); });
    ahours.addEventListener("input", overInfoAdmin);
    task.addEventListener("input", upd);
    loadTargets();
    f.addEventListener("submit", async ev => {
      ev.preventDefault();
      if (!btn || btn.disabled) return;
      const hv = parseInt(ahours.value || "0", 10);
      if (!(hv >= 1 && hv <= 720)) { toast("时长须在 1-720 小时", "err"); return; }
      btn.disabled = true;
      try {
        const d = await post("/admin/apply", new FormData(f));
        if (d.ok) {
          toast(d.msg || "代提交成功", "ok");
          setTimeout(() => (location.href = "/status"), 1200);
        } else { toast(d.error || "代提交失败", "err"); btn.disabled = false; }
      } catch (e) { toast(e.message || "网络错误", "err"); btn.disabled = false; }
    });
  }

  /* ---------- 时长快捷值（申请资源 / 代申请资源共用）----------
     它只做一件事：把 .hours-block 里的数字输入设成 chip 的值，再派发 input/change，
     让上面两套原有的校验（套餐 maxtime、1-720 范围、提交按钮可用性）照常跑 ——
     这里不复制任何校验逻辑。 */
  function wireHourChips() {
    document.addEventListener("click", ev => {
      const t = ev.target;
      const chip = t && t.closest ? t.closest(".chip[data-hours]") : null;
      if (!chip) return;
      const block = chip.closest(".hours-block");
      const input = block && block.querySelector('input[name="hours"]');
      if (!input) return;
      block.querySelectorAll(".chip").forEach(c => c.classList.toggle("is-on", c === chip));
      input.dataset.fromChip = "1";   // 让下面那个监听跳过这次高亮清理
      input.value = chip.dataset.hours;
      input.dispatchEvent(new Event("input", {bubbles: true}));
      input.dispatchEvent(new Event("change", {bubbles: true}));
    });
    // 手动改数字时取消 chip 高亮
    document.addEventListener("input", ev => {
      const input = ev.target;
      if (!input || !input.matches || !input.matches('.hours-block input[name="hours"]')) return;
      if (input.dataset.fromChip) { delete input.dataset.fromChip; return; }
      const block = input.closest(".hours-block");
      if (block) block.querySelectorAll(".chip.is-on").forEach(c => c.classList.remove("is-on"));
    });
  }

  /* ---------- 「复制」按钮 ----------
     注意：门户跑在 http://<内网IP>:8000 上，**不是安全上下文**，浏览器里根本没有
     navigator.clipboard —— 只用它的话点"复制"会静默失败。所以留 execCommand 兜底，
     并且现代 API 失败时（权限被拒、文档没聚焦…）也要回退，而不是直接报错。 */
  function legacyCopy(text) {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.top = "-1000px";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    ta.setSelectionRange(0, ta.value.length);   // iOS 上 select() 不够，要显式给范围
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    document.body.removeChild(ta);
    return ok;
  }

  function copyText(text) {
    const FAIL = "浏览器不允许自动复制，请手动选中命令";
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(text)
        .catch(() => { if (!legacyCopy(text)) throw new Error(FAIL); });
    }
    return legacyCopy(text) ? Promise.resolve() : Promise.reject(new Error(FAIL));
  }

  // 委托到 document：详情弹窗的内容是 innerHTML 后填的，逐个绑事件会漏。
  function wireCopyButtons() {
    document.addEventListener("click", async ev => {
      const t = ev.target;
      const btn = t && t.closest ? t.closest(".btn-copy") : null;
      if (!btn) return;
      const src = btn.dataset.copyFrom ? document.querySelector(btn.dataset.copyFrom) : null;
      const text = (btn.dataset.copy || (src ? src.textContent : "")).trim();
      if (!text) { toast("没有可复制的内容", "err"); return; }
      try {
        await copyText(text);
        toast("已复制", "ok");
      } catch (e) {
        toast(e.message || "复制失败", "err");
      }
    });
  }

  /* ---------- 我的镜像：注释 / 改名 / 删除（root 时作用于公共与他人镜像） ---------- */
  // 与助手 portal-ctl 的 note_len 同口径：表情/国旗/组合符号按"看得见的字符"算 1 个
  const NOTE_MAX = 64;

  function noteLen(str) {
    let n = 0, ri = false, join = false;
    for (const ch of str) {
      const cp = ch.codePointAt(0);
      if (cp === 0x200d) { join = true; continue; }
      if (cp === 0xfe0e || cp === 0xfe0f || (cp >= 0x1f3fb && cp <= 0x1f3ff)) continue;
      if ((cp >= 0x0300 && cp <= 0x036f) || (cp >= 0x1ab0 && cp <= 0x1aff) ||
          (cp >= 0x20d0 && cp <= 0x20ff) || (cp >= 0xfe20 && cp <= 0xfe2f)) continue;
      if (cp >= 0x1f1e6 && cp <= 0x1f1ff) {
        if (ri) { ri = false; continue; }
        ri = true; n += 1; continue;
      }
      ri = false;
      if (join) { join = false; continue; }
      n += 1;
    }
    return n;
  }

  // 注释列宽度固定：只有"真的放不下"（按渲染宽度）才显示「详情」，与字符个数无关
  function syncNoteMore() {
    $$("#img-table .note-td").forEach(td => {
      const cell = td.querySelector(".note-cell"), more = td.querySelector(".note-more");
      if (cell && more) more.hidden = cell.scrollWidth <= cell.clientWidth + 1;
    });
  }

  function wireImagePage() {
    const table = $("#img-table");
    if (!table) return;
    syncNoteMore();
    window.addEventListener("resize", syncNoteMore);
    table.addEventListener("click", async ev => {
      // 注释列：只显示固定宽度，点开看完整内容
      const nv = ev.target && ev.target.closest ? ev.target.closest("[data-note-full]") : null;
      if (nv) {
        $("#note-title").textContent = nv.dataset.noteTitle || "";
        $("#note-body").textContent = nv.dataset.noteFull || "";
        $("#note-modal").hidden = false;
        return;
      }
      const btn = ev.target && ev.target.closest
        ? ev.target.closest("[data-img-note],[data-img-rename],[data-img-delete]") : null;
      if (!btn) return;
      const name = btn.dataset.imgNote || btn.dataset.imgRename || btn.dataset.imgDelete || "";
      const owner = btn.dataset.owner || "";          // root 视图才有：用户名 或 public
      const base = owner ? "/admin/images/" + encodeURIComponent(owner) : "/images";
      btn.disabled = true;
      const done = msg => { toast(msg, "ok"); setTimeout(() => location.reload(), 700); };
      try {
        if (btn.dataset.imgNote !== undefined) {
          const cur = btn.dataset.note || "";
          const note = await askText({
            title: "镜像注释",
            label: name + ".sqsh",
            value: cur,
            placeholder: "留空 = 清空注释",
            hint: "支持中文/表情/空格，连续空格会合并成一个；表情算 1 个字符",
            counter: v => noteLen(v) + " / " + NOTE_MAX,
            okText: "保存注释",
            validate: v => noteLen(v) > NOTE_MAX
              ? "最多 " + NOTE_MAX + " 个字符（当前 " + noteLen(v) + " 个）" : "",
          });
          if (note === null) { btn.disabled = false; return; }
          const fd = new FormData();
          fd.append("note", note);
          const r = await post(base + "/" + encodeURIComponent(name) + "/comment", fd);
          if (!r || !r.ok) throw new Error((r && r.error) || "保存注释失败");
          done(r.msg || "注释已保存");
          return;
        }
        if (btn.dataset.imgRename) {
          // 公共镜像的名字允许点/横线（cuda12.8.0-devel-ubuntu24.04），个人镜像仍只允许英文/数字/下划线
          const pub = owner === "public";
          const rule = pub ? PUBLIC_IMG_NAME_RE_JS : IMG_NAME_RE_JS;
          const hint = pub ? "英文/数字/点/横线/下划线/加号，1-128 位，不能以点开头"
                           : IMG_NAME_HINT;
          const input = await askText({
            title: pub ? "重命名公共镜像" : "重命名镜像",
            label: name + ".sqsh →",
            value: name,
            hint: "只能包含" + hint,
            okText: "改名",
            validate: v => {
              const t = v.trim().replace(/\.sqsh$/i, "");
              if (!t) return "请填写新名称";
              if (!rule.test(t)) return "只能包含" + hint;
              if (t === name) return "新名称与原名相同";
              return "";
            },
          });
          if (input === null) { btn.disabled = false; return; }
          const newName = input.trim().replace(/\.sqsh$/i, "");
          const fd = new FormData();
          fd.append("new_name", newName);
          const r = await post(base + "/" + encodeURIComponent(name) + "/rename", fd);
          if (!r || !r.ok) throw new Error((r && r.error) || "改名失败");
          done(r.msg || "已改名");
          return;
        }
        const size = btn.dataset.imgSize ? "（" + btn.dataset.imgSize + "）" : "";
        const who = owner && owner !== "public" ? "（" + owner + " 的个人镜像）" : "";
        const goDel = await askConfirm({
          title: "删除镜像", okText: "删除", danger: true,
          text: "确定删除镜像 " + name + ".sqsh" + size + who
                + "？\n删除后无法恢复，也不能再用它「重新启动」历史资源。",
        });
        if (!goDel) { btn.disabled = false; return; }
        const r = await post(base + "/" + encodeURIComponent(name) + "/delete", new FormData());
        if (!r || !r.ok) throw new Error((r && r.error) || "删除失败");
        done(r.msg || "已删除");
      } catch (e) {
        toast(e.message || "操作失败", "err");
        btn.disabled = false;
      }
    });
  }

  /* ---------- 启动 ---------- */
  document.addEventListener("DOMContentLoaded", () => {
    wireDialog();
    wireGeneric();
    wireMyPage();
    wireApplyForm();
    wireAdminApply();
    wireHourChips();
    wireCopyButtons();
    wireImagePage();
  });
})();
