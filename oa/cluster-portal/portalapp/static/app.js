/* OrbitCluster 集群门户前端脚本 */
"use strict";
(function () {
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));

  const ACTIVE_STATES = ["PENDING", "RUNNING", "SUSPENDED", "COMPLETING", "STOPPING"];

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
        if (!window.confirm(msg)) return;
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
      m.addEventListener("click", ev => { if (ev.target === m) m.hidden = true; });
    });
    $$("#btn-refresh").forEach(b => b.addEventListener("click", () => location.reload()));

    /* 管理员改配额：弹出输入框（例 100G/500G/1T）后提交 */
    $$(".act-quota").forEach(b => {
      b.addEventListener("click", async () => {
        const user = b.dataset.user || "";
        const size = window.prompt("为 " + user + " 设置 /share 磁盘配额（软=硬，例：100G / 500G / 1T）：", "");
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
    return `<tr data-id="${i.id}">
      <td class="mono">${esc(i.job_id)}</td>
      <td><b>${esc(i.res_name)}</b>${sub ? `<div class="small muted">${esc(sub)}</div>` : ""}${saveLine}</td>
      <td><span class="st st-${esc(String(i.state).toLowerCase())}">${esc(i.state_cn)}</span></td>
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
    async function load() {
      try {
        const list = await get("/api/my/instances");
        const body = $("#inst-body");
        const empty = $("#inst-empty");
        if (!list.length) { body.innerHTML = ""; if (empty) empty.hidden = false; return; }
        if (empty) empty.hidden = true;
        last = {};
        body.innerHTML = list.map(i => { last[i.id] = i; return rowHtml(i); }).join("");
        const hasActive = list.some(i => i.can_stop);
        if (!hasActive) clearInterval(window._poll);
      } catch (e) { /* 忽略瞬时错误 */ }
    }
    load();
    window._poll = setInterval(load, 15000);
    $("#inst-body").addEventListener("click", async ev => {
      const b = ev.target.closest("button");
      if (!b) return;
      const id = b.dataset.id;
      if (b.classList.contains("act-log")) {
        $("#log-modal").hidden = false;
        $("#log-title").textContent = "（作业 #" + id + "）";
        $("#log-body").textContent = "加载中…";
        try {
          const d = await get("/instances/" + id + "/log?lines=300");
          $("#log-body").textContent = d.ok ? d.text : ("错误：" + d.error);
        } catch (e) { $("#log-body").textContent = "读取失败：" + e.message; }
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
          <pre class="cmd-preview">${esc(sshNode)}</pre>
          ${nodeLine}
          <p class="muted small">用你登记公钥对应的<b>私钥</b>连接；首次连接若提示 host key 变化，
          因容器 host key 存放在你的家目录（.ssh-hostkeys），更换机器/清理后需重新接受。</p>
          <h3>本次提交的命令</h3>
          <pre class="cmd-preview">${esc(i.cmd || "")}</pre>`;
      } else if (b.classList.contains("act-save")) {
        const rec = last[id] || {};
        let name = window.prompt("保存当前容器状态为个人镜像（保存到 /share/images/" +
          (rec.username || "") + "/<名称>.sqsh，计入你的 /share 配额）\n名称规则：仅英文/数字/下划线，1-64 位",
          rec.res_name ? String(rec.res_name).replace(/[^A-Za-z0-9_]/g, "_").slice(0, 40) : "");
        if (name === null) return;
        name = name.trim();
        if (!/^[A-Za-z0-9_]{1,64}$/.test(name)) {
          toast("镜像名只能包含英文/数字/下划线（1-64 位）", "err");
          return;
        }
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
            if (!window.confirm(d.error + "，是否覆盖？")) { b.disabled = false; return; }
            d = await doSave(true);
          }
          toast(d.msg || (d.ok ? "已开始保存镜像" : d.error), d.ok ? "ok" : "err");
          if (d.ok) setTimeout(load, 1200);
        } catch (e) { toast(e.message || "网络错误", "err"); }
        b.disabled = false;
      } else if (b.classList.contains("act-stop")) {
        if (!window.confirm("确定停止该资源（作业 #" + (last[id] ? last[id].job_id : id) + "）？"
          + "\n运行中的容器会被终止，端口随即释放。")) return;
        b.disabled = true;
        try {
          const d = await post("/instances/" + id + "/stop", new FormData());
          toast(d.msg || (d.ok ? "已停机" : d.error), d.ok ? "ok" : "err");
          if (d.ok) setTimeout(load, 1800);
        } catch (e) { toast(e.message, "err"); }
        b.disabled = false;
      } else if (b.classList.contains("act-restart")) {
        const rec = last[id] || {};
        if (!window.confirm("确定按原参数重新启动该资源？\n作业 #" + (rec.job_id || id)
          + " 将重新入队（镜像/套餐资源/端口/时长/任务名不变）。")) return;
        b.disabled = true;
        try {
          const d = await post("/instances/" + id + "/restart", new FormData());
          toast(d.msg || (d.ok ? "已重新启动" : d.error), d.ok ? "ok" : "err");
          if (d.ok) setTimeout(load, 1500);
        } catch (e) { toast(e.message, "err"); }
        b.disabled = false;
      } else if (b.classList.contains("act-delinst")) {
        const rec = last[id] || {};
        if (!window.confirm("确定删除这条已停止/结束的资源记录？\n作业 #" + (rec.job_id || id)
          + " 的记录与日志文件将被删除（不可恢复）。")) return;
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

  /* ---------- 启动 ---------- */
  document.addEventListener("DOMContentLoaded", () => {
    wireGeneric();
    wireMyPage();
    wireApplyForm();
    wireAdminApply();
  });
})();
