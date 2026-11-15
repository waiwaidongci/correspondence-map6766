/* 隐私分组发布前端（原生 JS，零依赖） */
"use strict";

const state = {
  studies: [],
  current: null,          // 当前研究
  publications: [],
  rowGroups: [],          // [[idx...], ...]
  colGroups: [],
  extraSupp: new Set(),   // "a,b"
  preview: null,
  modalMode: "create",    // create | revise
};

const $ = (sel) => document.querySelector(sel);

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `请求失败 ${res.status}`);
  return data;
}

function toast(msg, kind = "") {
  const el = $("#toast");
  el.textContent = msg;
  el.className = `toast ${kind}`;
  el.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { el.hidden = true; }, 3200);
}

// ---------------- 研究列表 ----------------
async function loadStudies() {
  state.studies = await api("/api/studies");
  const box = $("#study-list");
  if (!state.studies.length) {
    box.innerHTML = `<div class="empty">暂无研究<br/>点击右上角新建或载入演示</div>`;
    return;
  }
  box.innerHTML = "";
  state.studies.forEach((s) => {
    const div = document.createElement("div");
    div.className = "study-item" + (state.current && s.id === state.current.id ? " active" : "");
    const badge = s.status === "active"
      ? `<span class="badge badge-active">在用</span>`
      : `<span class="badge badge-retired">已停用</span>`;
    div.innerHTML = `
      <div class="name">${escapeHtml(s.name)} ${badge}</div>
      <div class="meta">${s.row_labels.length} 行 × ${s.col_labels.length} 列
        · 阈值 ${s.threshold} · 发布 ${s.publication_count} 次</div>`;
    div.onclick = () => selectStudy(s.id);
    box.appendChild(div);
  });
}

async function selectStudy(id) {
  state.current = await api(`/api/studies/${id}`);
  state.preview = null;
  $("#preview-area").innerHTML = "";
  initGroups();
  await Promise.all([loadStudies(), loadPublications(), renderStudy()]);
}

// ---------------- 底表渲染 ----------------
function renderStudy() {
  const s = state.current;
  $("#study-head").innerHTML = `
    <div class="study-head-meta">
      <h2>${escapeHtml(s.name)}</h2>
      ${s.status === "active"
        ? `<span class="badge badge-active">在用</span>`
        : `<span class="badge badge-retired">已停用 · 底表校订后不可再发布</span>`}
      <span class="pill">总量 ${grand(s.base_table)}</span>
      <span class="pill">保护格 ${countProtected(s)} 个（0 &lt; 计数 &lt; ${s.threshold}）</span>
      <button class="btn btn-sm" id="btn-revise">校订底表</button>
    </div>
    ${s.description ? `<p class="pill">${escapeHtml(s.description)}</p>` : ""}`;
  $("#btn-revise").onclick = openRevise;

  $("#card-base").hidden = false;
  $("#card-group").hidden = false;
  renderBaseTable();
  renderGroupEditors();
  renderSuppBlocks();
}

function grand(t) { return t.reduce((a, r) => a + r.reduce((b, x) => b + x, 0), 0); }
function countProtected(s) {
  return s.base_table.flat().filter((x) => 0 < x && x < s.threshold).length;
}
function isProtected(s, i, j) {
  const x = s.base_table[i][j];
  return 0 < x && x < s.threshold;
}

function renderBaseTable() {
  const s = state.current;
  let html = `<table class="grid"><caption>红色为必须保护的小格（正计数低于阈值）</caption>
    <tr><th></th>${s.col_labels.map((c) => `<th>${escapeHtml(c)}</th>`).join("")}
    <th>行合计</th></tr>`;
  s.base_table.forEach((row, i) => {
    html += `<tr><th>${escapeHtml(s.row_labels[i])}</th>`;
    row.forEach((x, j) => {
      const cls = isProtected(s, i, j) ? "protected" : "";
      html += `<td class="${cls}">${x}</td>`;
    });
    html += `<td class="marginal">${row.reduce((a, b) => a + b, 0)}</td></tr>`;
  });
  html += `<tr><th>列合计</th>`;
  s.col_labels.forEach((_, j) => {
    html += `<td class="marginal">${s.base_table.reduce((a, r) => a + r[j], 0)}</td>`;
  });
  html += `<td class="marginal">${grand(s.base_table)}</td></tr></table>
  <div class="legend">
    <span class="lg-protect">保护格（正计数 &lt; ${s.threshold}）</span>
    <span class="lg-suppress">对外隐去块</span>
  </div>`;
  $("#base-table").innerHTML = html;
}

// ---------------- 分组编辑 ----------------
function initGroups() {
  const s = state.current;
  // 默认：每类各自成组（最细）
  state.rowGroups = s.row_labels.map((_, i) => [i]);
  state.colGroups = s.col_labels.map((_, j) => [j]);
  state.extraSupp = new Set();
}

function renderGroupEditors() {
  renderAxisGroups("#row-groups", state.rowGroups, state.current.row_labels, "row");
  renderAxisGroups("#col-groups", state.colGroups, state.current.col_labels, "col");
}

function renderAxisGroups(sel, groups, labels, axis) {
  const box = $(sel);
  box.innerHTML = "";
  // 每个类别一个 chip，点击切换它所在的组（在相邻组间移动实现合并/拆分）
  const memberOf = {};
  groups.forEach((g, gi) => g.forEach((idx) => { memberOf[idx] = gi; }));

  groups.forEach((g, gi) => {
    const row = document.createElement("div");
    row.className = "group-row";
    row.innerHTML = `<span class="group-label">组 ${gi + 1}（点击类别可移入下一组/合并）</span>`;
    g.forEach((idx) => {
      const chip = document.createElement("span");
      chip.className = "group-chip in-group";
      chip.textContent = labels[idx];
      chip.onclick = () => moveMember(axis, gi, idx);
      row.appendChild(chip);
    });
    box.appendChild(row);
  });
}

function moveMember(axis, fromGroup, idx) {
  const groups = axis === "row" ? state.rowGroups : state.colGroups;
  // 移到下一组；若已是最后一组则新建组（拆分）
  if (groups.length === 1 || fromGroup === groups.length - 1) {
    groups.push([idx]);
  } else {
    groups[fromGroup + 1].push(idx);
    groups[fromGroup + 1].sort((a, b) => a - b);
  }
  groups[fromGroup] = groups[fromGroup].filter((x) => x !== idx);
  // 删除空组
  const cleaned = groups.filter((g) => g.length);
  if (axis === "row") state.rowGroups = cleaned; else state.colGroups = cleaned;
  state.extraSupp.clear();
  state.preview = null;
  $("#preview-area").innerHTML = "";
  renderGroupEditors();
  renderSuppBlocks();
}

// ---------------- 补隐块 ----------------
function renderSuppBlocks() {
  const s = state.current;
  const nrg = state.rowGroups.length, ncg = state.colGroups.length;
  const box = $("#supp-blocks");
  box.innerHTML = "";
  // 预计算强制隐去块（含保护格）
  const forced = forcedBlocks(s);
  for (let a = 0; a < nrg; a++) {
    for (let b = 0; b < ncg; b++) {
      const key = `${a},${b}`;
      const chip = document.createElement("span");
      const isForced = forced.has(key);
      chip.className = "supp-chip" + (isForced ? " forced" : state.extraSupp.has(key) ? " on" : "");
      chip.textContent = isForced ? `块${a + 1}-${b + 1}（强制）` : `块${a + 1}-${b + 1}`;
      if (!isForced) {
        chip.onclick = () => {
          if (state.extraSupp.has(key)) state.extraSupp.delete(key);
          else state.extraSupp.add(key);
          renderSuppBlocks();
        };
      }
      box.appendChild(chip);
    }
  }
}

function forcedBlocks(s) {
  const set = new Set();
  state.rowGroups.forEach((grpR, a) => {
    state.colGroups.forEach((grpC, b) => {
      if (grpR.some((i) => grpC.some((j) => isProtected(s, i, j)))) {
        set.add(`${a},${b}`);
      }
    });
  });
  return set;
}

function buildPayload() {
  return {
    title: $("#pub-title").value || "未命名发布",
    row_groups: state.rowGroups,
    col_groups: state.colGroups,
    extra_suppressed: [...state.extraSupp].map((k) => k.split(",").map(Number)),
  };
}

// ---------------- 预览 / 发布 ----------------
async function doPreview() {
  if (!guardActive()) return;
  try {
    const p = await api(`/api/studies/${state.current.id}/preview`, {
      method: "POST", body: buildPayload(),
    });
    state.preview = p;
    renderResult(p, true);
    toast("预览完成：未写入发布历史", "ok");
  } catch (e) { toast(e.message, "err"); }
}

async function doPublish() {
  if (!guardActive()) return;
  if (!confirm("确认正式发布？该发布将计入历史，并参与后续所有发布的联立安全判断。")) return;
  try {
    const p = await api(`/api/studies/${state.current.id}/publish`, {
      method: "POST", body: buildPayload(),
    });
    state.preview = p;
    renderResult(p, false);
    if (p.safe) toast("发布成功，已计入历史", "ok");
    else toast("发布被拒绝：存在可被唯一确定的保护格", "err");
    await Promise.all([loadStudies(), loadPublications()]);
  } catch (e) { toast(e.message, "err"); }
}

function guardActive() {
  if (!state.current) { toast("请先选择研究", "err"); return false; }
  if (state.current.status !== "active") {
    toast("该研究已停用，请在校订产生的新研究上发布", "err"); return false;
  }
  return true;
}

// ---------------- 结果渲染 ----------------
function renderResult(p, isPreview) {
  const s = state.current;
  const tag = isPreview ? "预览（不占历史）" : `正式发布 #${p.seq ?? ""}`;
  const banner = p.safe
    ? `<div class="result-banner result-ok">✓ ${tag}：可发布 —— 每个保护格都至少有 2 个可行整数</div>`
    : `<div class="result-banner result-bad">✗ ${tag}：拒绝发布<ul>
        ${p.reject_reasons.map((r) => `<li>${escapeHtml(r)}</li>`).join("")}</ul></div>`;

  // 脱敏分组表
  const rgLabels = p.row_groups.map((g) => g.map((i) => s.row_labels[i]).join("|"));
  const cgLabels = p.col_groups.map((g) => g.map((j) => s.col_labels[j]).join("|"));
  const suppSet = new Set(p.suppressed_blocks.map(([a, b]) => `${a},${b}`));
  let table = `<div class="card"><h3>对外脱敏分组计数${isPreview ? "（预览）" : ""}</h3>
    <table class="grid"><tr><th>分组块</th>
    ${cgLabels.map((c) => `<th>${escapeHtml(c)}</th>`).join("")}<th>行组合计</th></tr>`;
  p.released_table.forEach((line, a) => {
    table += `<tr><th>${escapeHtml(rgLabels[a])}</th>`;
    line.forEach((v, b) => {
      if (v === null) table += `<td class="suppressed">隐去</td>`;
      else table += `<td>${v}</td>`;
    });
    table += `<td class="marginal">${p.row_marginals[a]}</td></tr>`;
  });
  table += `<tr><th>列组合计</th>`;
  p.col_marginals.forEach((v) => { table += `<td class="marginal">${v}</td>`; });
  table += `<td class="marginal">${p.grand_total}</td></tr></table>
    <div class="legend"><span class="lg-suppress">隐去块（含保护格或补隐）</span></div></div>`;

  // 逐保护格可行范围
  let ranges = `<div class="card"><h3>联立本次与全部既往发布后 · 逐保护格可行整数范围</h3>
    <p class="pill">约束等式 ${p.n_equations ?? "-"} 条 · 合并发布 ${p.n_publications ?? "-"} 次
    （可行值数 ≥ 2 才准发布）</p>
    <table class="grid range-table"><tr><th>保护格</th><th>真实计数（内部）</th>
    <th>可行最小值</th><th>可行最大值</th><th>可能值数</th><th>判定</th></tr>`;
  p.ranges.forEach((r) => {
    const ok = r.possible_values >= 2;
    ranges += `<tr><td>${escapeHtml(r.row)} × ${escapeHtml(r.col)}</td>
      <td class="protected">${r.true_value}</td><td>${r.min}</td><td>${r.max}</td>
      <td>${r.possible_values}</td>
      <td class="${ok ? "safe" : "unsafe"}">${ok ? "安全" : "唯一可定·拒绝"}</td></tr>`;
  });
  if (!p.ranges.length) ranges += `<tr><td colspan="6" class="empty">无保护格</td></tr>`;
  ranges += `</table></div>`;

  $("#preview-area").innerHTML = banner + table + ranges;
}

// ---------------- 历史 ----------------
async function loadPublications() {
  if (!state.current) { $("#history-list").innerHTML = ""; return; }
  state.publications = await api(`/api/studies/${state.current.id}/publications`);
  const box = $("#history-list");
  if (!state.publications.length) {
    box.innerHTML = `<div class="empty">尚无发布<br/>预览不会出现在这里</div>`;
    return;
  }
  box.innerHTML = "";
  state.publications.forEach((p) => {
    const div = document.createElement("div");
    div.className = "history-item";
    const badge = p.safe
      ? `<span class="badge badge-safe">已发布</span>`
      : `<span class="badge badge-reject">已拒绝</span>`;
    div.innerHTML = `
      <div class="h-title">#${p.seq} ${escapeHtml(p.title)} ${badge}</div>
      <div class="h-meta">${p.created_at || ""}<br/>
        ${p.row_groups.length} 行组 × ${p.col_groups.length} 列组 ·
        隐去 ${p.suppressed_blocks.length} 块 · 总量 ${p.grand_total}</div>
      <div class="h-actions">
        ${p.safe ? `<button class="btn btn-sm dl-btn" data-id="${p.id}">下载脱敏 CSV</button>` : ""}
        <button class="btn btn-sm view-btn" data-seq="${p.seq}">查看范围</button>
      </div>`;
    box.appendChild(div);
  });
  box.querySelectorAll(".dl-btn").forEach((b) => {
    b.onclick = () => { window.location = `/api/download/${b.dataset.id}`; };
  });
  box.querySelectorAll(".view-btn").forEach((b) => {
    b.onclick = () => {
      const p = state.publications.find((x) => x.seq === Number(b.dataset.seq));
      renderResult(p, false);
      window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
    };
  });
}

// ---------------- 新建 / 校订弹窗 ----------------
function openModal(mode) {
  state.modalMode = mode;
  $("#modal-error").hidden = true;
  if (mode === "create") {
    $("#modal-title").textContent = "新建研究（底表）";
    $("#m-name").value = ""; $("#m-desc").value = "";
    $("#m-threshold").value = 5;
    $("#m-rows").value = "家庭同行,学习小组,独立参访,策展同业";
    $("#m-cols").value = "人工讲解,语音导览,自助地图,团体预约,线上预展";
    $("#m-matrix").value = "2,12,30,40,18\n1,25,35,45,22\n10,20,3,50,27\n15,22,33,4,19";
  } else {
    const s = state.current;
    $("#modal-title").textContent = "校订底表（有历史发布将停用本研究并克隆新研究）";
    $("#m-name").value = s.name; $("#m-desc").value = s.description;
    $("#m-threshold").value = s.threshold;
    $("#m-rows").value = s.row_labels.join(",");
    $("#m-cols").value = s.col_labels.join(",");
    $("#m-matrix").value = s.base_table.map((r) => r.join(",")).join("\n");
  }
  $("#modal").hidden = false;
}
function openRevise() { openModal("revise"); }

function closeModal() { $("#modal").hidden = true; }

function parseMatrix(text, rows, cols) {
  const lines = text.trim().split(/\n+/).map((l) => l.trim()).filter(Boolean);
  if (lines.length !== rows) throw new Error(`矩阵行数应为 ${rows}`);
  return lines.map((line) => {
    const cells = line.split(/[,，\s]+/).filter(Boolean).map(Number);
    if (cells.length !== cols) throw new Error(`每行应有 ${cols} 个整数`);
    if (cells.some((x) => !Number.isInteger(x))) throw new Error("存在非整数");
    return cells;
  });
}

async function saveModal() {
  const errEl = $("#modal-error");
  try {
    const rowLabels = $("#m-rows").value.split(/[,，]/).map((s) => s.trim()).filter(Boolean);
    const colLabels = $("#m-cols").value.split(/[,，]/).map((s) => s.trim()).filter(Boolean);
    const threshold = Number($("#m-threshold").value);
    const matrix = parseMatrix($("#m-matrix").value, rowLabels.length, colLabels.length);

    if (state.modalMode === "create") {
      const s = await api("/api/studies", {
        method: "POST",
        body: {
          name: $("#m-name").value.trim(), description: $("#m-desc").value.trim(),
          row_labels: rowLabels, col_labels: colLabels,
          base_table: matrix, threshold,
        },
      });
      closeModal();
      toast("研究已创建", "ok");
      await loadStudies();
      await selectStudy(s.id);
    } else {
      const out = await api(`/api/studies/${state.current.id}/revise`, {
        method: "POST",
        body: {
          row_labels: rowLabels, col_labels: colLabels,
          base_table: matrix, threshold,
        },
      });
      closeModal();
      if (out.cloned) {
        toast(`原研究已停用，已创建新研究「${out.new_study.name}」`, "ok");
      } else {
        toast("底表已原地校订（无历史发布）", "ok");
      }
      await loadStudies();
      await selectStudy(out.new_study.id);
    }
  } catch (e) {
    errEl.textContent = e.message;
    errEl.hidden = false;
  }
}

// ---------------- 演示数据 ----------------
async function seedDemo() {
  try {
    const demo = {
      name: `演示研究 ${new Date().toLocaleString("zh-CN")}`,
      description: "含多个低于阈值的保护格，用于演练分组发布与联立推断",
      row_labels: ["家庭同行", "学习小组", "独立参访", "策展同业"],
      col_labels: ["人工讲解", "语音导览", "自助地图", "团体预约", "线上预展"],
      base_table: [
        [2, 12, 30, 40, 18],
        [1, 25, 35, 45, 22],
        [10, 20, 3, 50, 27],
        [15, 22, 33, 4, 19],
      ],
      threshold: 5,
    };
    const s = await api("/api/studies", { method: "POST", body: demo });
    await loadStudies();
    await selectStudy(s.id);
    toast("演示数据已载入", "ok");
  } catch (e) { toast(e.message, "err"); }
}

function escapeHtml(str) {
  return String(str).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ---------------- 初始化 ----------------
window.addEventListener("DOMContentLoaded", () => {
  $("#btn-new-study").onclick = () => openModal("create");
  $("#btn-seed").onclick = seedDemo;
  $("#btn-preview").onclick = doPreview;
  $("#btn-publish").onclick = doPublish;
  $("#modal-cancel").onclick = closeModal;
  $("#modal-save").onclick = saveModal;
  loadStudies().catch((e) => toast(e.message, "err"));
});
