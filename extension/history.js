// history.js — full-page command center: pipeline stats + paginated, filterable
// application history. Data: GET /api/history (rows) + /api/history/stats (overview).
// PDFs open inline (server sends Content-Disposition: inline); diff.md is fetched
// and rendered as styled markdown in a modal.

const API_BASE = "http://127.0.0.1:13450";
const PAGE_SIZE = 20;

const state = {
  query: "",
  status: "",
  page: 0, // 0-based
};

const el = (id) => document.getElementById(id);

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

// Defensive ATS normalisation: never render null/missing as 0, clamp to 0–100.
function normScore(v) {
  if (v == null || v === "") return null;
  const n = Number(v);
  if (Number.isNaN(n)) return null;
  return Math.max(0, Math.min(100, Math.round(n)));
}

// Engine timings arrive in milliseconds. Below a second the raw figure is more
// informative (820ms), above it seconds read faster; under 10s the extra decimal
// is worth keeping (6.00s) since that is where the pipeline actually lives.
// Rows logged before timings were recorded carry null — shown as "–", never as
// a zero that would read as an instant run.
function fmtMs(ms) {
  if (ms == null || ms === "") return null;
  const n = Number(ms);
  if (Number.isNaN(n) || n <= 0) return null;
  if (n < 1000) return `${Math.round(n)}ms`;
  return `${(n / 1000).toFixed(n < 10000 ? 2 : 1)}s`;
}

// ---------------------------------------------------------------------------
// Minimal markdown renderer (headings / lists / bold / code / pipe tables).
// The diff report is a known, constrained format, so a small renderer keeps the
// extension dependency-free and renders it nicely instead of raw markdown text.
// ---------------------------------------------------------------------------
function inlineMd(s) {
  let t = escapeHtml(s);
  t = t.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  t = t.replace(/`([^`]+)`/g, "<code>$1</code>");
  t = t.replace(/(^|[^*])\*([^*]+)\*/g, "$1<em>$2</em>");
  return t;
}

function tableToHtml(rows) {
  const cell = (s, tag) =>
    s.replace(/^\|/, "").replace(/\|$/, "").split("|")
      .map((c) => `<${tag}>${inlineMd(c.trim())}</${tag}>`).join("");
  let head = "";
  const body = [];
  let headerSet = false;
  for (const row of rows) {
    if (/^\|[\s:|-]+$/.test(row)) continue; // separator row | --- | --- |
    if (!headerSet) { head = cell(row, "th"); headerSet = true; }
    else body.push(`<tr>${cell(row, "td")}</tr>`);
  }
  const thead = head ? `<thead><tr>${head}</tr></thead>` : "";
  return `<table>${thead}<tbody>${body.join("")}</tbody></table>`;
}

function renderMarkdown(md) {
  const lines = md.replace(/\r\n/g, "\n").split("\n");
  const out = [];
  let listOpen = null; // null | "ul" | "ol"
  const closeList = () => { if (listOpen) { out.push(`</${listOpen}>`); listOpen = null; } };

  let i = 0;
  while (i < lines.length) {
    const trimmed = lines[i].trim();
    if (!trimmed) { closeList(); i++; continue; }

    // Pipe table — consume consecutive | lines.
    if (trimmed.startsWith("|")) {
      closeList();
      const rows = [];
      let j = i;
      while (j < lines.length && lines[j].trim().startsWith("|")) {
        rows.push(lines[j].trim());
        j++;
      }
      i = j;
      out.push(tableToHtml(rows));
      continue;
    }

    // Headings
    const h = trimmed.match(/^(#{1,6})\s+(.*)$/);
    if (h) { closeList(); out.push(`<h${h[1].length}>${inlineMd(h[2])}</h${h[1].length}>`); i++; continue; }

    // Horizontal rule
    if (/^(-{3,}|\*{3,})$/.test(trimmed)) { closeList(); out.push("<hr>"); i++; continue; }

    // Unordered list
    if (trimmed.startsWith("- ") || trimmed.startsWith("* ")) {
      if (listOpen !== "ul") { closeList(); out.push("<ul>"); listOpen = "ul"; }
      out.push(`<li>${inlineMd(trimmed.slice(2))}</li>`);
      i++; continue;
    }
    // Ordered list
    if (/^\d+\.\s/.test(trimmed)) {
      if (listOpen !== "ol") { closeList(); out.push("<ol>"); listOpen = "ol"; }
      out.push(`<li>${inlineMd(trimmed.replace(/^\d+\.\s/, ""))}</li>`);
      i++; continue;
    }

    // Plain paragraph
    closeList();
    out.push(`<p>${inlineMd(trimmed)}</p>`);
    i++;
  }
  closeList();
  return out.join("\n");
}

// ---------------------------------------------------------------------------
// ATS "signal" meter (~85+ strong, ~70 mid, else low; null → not scored)
// ---------------------------------------------------------------------------
function atsCell(score) {
  const n = normScore(score);
  if (n == null) {
    return `<div class="ats na" title="Not scored"><div class="ats-line"><span class="ats-num">–</span></div><div class="ats-meter"><i style="width:0%"></i></div></div>`;
  }
  const cls = n >= 85 ? "hi" : n >= 70 ? "mid" : "lo";
  const desc = n >= 85 ? "strong match" : n >= 70 ? "moderate match" : "needs improvement";
  return `<div class="ats ${cls}" title="ATS signal ${n}/100 — ${desc}"><div class="ats-line"><span class="ats-num">${n}</span><span class="ats-unit">%</span></div><div class="ats-meter"><i style="width:${Math.min(100, n)}%"></i></div></div>`;
}

// ---------------------------------------------------------------------------
// Runtime cell — how long the engine took to produce this resume, split into
// model time and local compile. The tagline: latency has no good/bad
// thresholds, so it stays neutral, unlike the colour-coded ATS signal.
// ---------------------------------------------------------------------------
function runtimeCell(a) {
  const total = fmtMs(a.total_time_ms);
  if (total == null) {
    return `<div class="runtime" title="No timings recorded for this run">–</div>`;
  }
  const parts = [];
  const llm = fmtMs(a.llm_time_ms);
  const compile = fmtMs(a.compile_time_ms);
  if (llm) parts.push(`llm ${llm}`);
  if (compile) parts.push(`compile ${compile}`);
  const split = parts.length ? `<span class="rt-split">${parts.join(" · ")}</span>` : "";
  const tip = parts.length ? ` — ${parts.join(", ")}` : "";
  return `<div class="runtime" title="Total wall-clock${tip}"><span class="rt-total">${total}</span>${split}</div>`;
}

// ---------------------------------------------------------------------------
// Pipeline overview (Total · Interview · Offers · Avg Signal · Avg runtime)
// ---------------------------------------------------------------------------
async function loadStats() {
  try {
    const res = await fetch(`${API_BASE}/api/history/stats`);
    if (!res.ok) throw new Error("stats error");
    renderStats(await res.json());
  } catch (err) {
    // leave the skeleton placeholders in place
  }
}

function renderStats(s) {
  const setVal = (sel, txt) => {
    const n = el("stats").querySelector(sel);
    if (n) { n.classList.remove("skeleton"); n.textContent = txt; }
  };
  setVal(".i-total .val", s.total ?? 0);
  setVal(".i-interview .val", s.interview ?? 0);
  setVal(".i-offered .val", s.offered ?? 0);

  const avg = normScore(s.avg_ats);
  const stat = el("stats").querySelector(".i-ats");
  const avgEl = stat?.querySelector(".val");
  const signal = stat?.querySelector(".signal i");
  if (avgEl) avgEl.classList.remove("skeleton");
  if (avg == null) {
    if (avgEl) avgEl.textContent = "–";
    if (stat) stat.setAttribute("data-level", "na");
    if (signal) signal.style.width = "0%";
  } else {
    if (avgEl) avgEl.innerHTML = `${avg}<span class="u">%</span>`;
    if (stat) stat.setAttribute("data-level", avg >= 85 ? "hi" : avg >= 70 ? "mid" : "lo");
    if (signal) signal.style.width = `${Math.min(100, avg)}%`;
  }

  renderRuntime(s);
}

// Average wall-clock per application, with the model/compile split underneath.
// Rows recorded before timings were captured have no value, so the tile says so
// rather than reporting a zero that looks like an instant run.
function renderRuntime(s) {
  const total = fmtMs(s.avg_total_ms);
  const valEl = el("stats").querySelector(".i-runtime .val");
  const subEl = el("runtimeBreakdown");
  if (valEl) valEl.classList.remove("skeleton");
  if (total == null) {
    if (valEl) valEl.textContent = "–";
    if (subEl) subEl.textContent = "no timings recorded";
    return;
  }
  if (valEl) valEl.textContent = total;

  const parts = [];
  const llm = fmtMs(s.avg_llm_ms);
  const compile = fmtMs(s.avg_compile_ms);
  if (llm) parts.push(`llm ${llm}`);
  if (compile) parts.push(`compile ${compile}`);
  if (subEl) subEl.textContent = parts.join(" · ") || "no timings recorded";
}

async function load() {
  loadStats();
  const url =
    `${API_BASE}/api/history?limit=${PAGE_SIZE}&offset=${state.page * PAGE_SIZE}` +
    (state.query ? `&company=${encodeURIComponent(state.query)}&role=${encodeURIComponent(state.query)}` : "") +
    (state.status ? `&status=${state.status}` : "");
  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error("server error");
    const data = await res.json();
    render(data.applications || [], data.total || 0);
  } catch (err) {
    el("rows").innerHTML = "";
    el("empty").style.display = "block";
    el("emptyTitle").textContent = "Can't reach the engine";
    el("emptyHint").innerHTML = `Is <code>fs serve</code> running?`;
    el("count").textContent = "offline";
  }
}

const PDF_SVG = '<svg width="11" height="11" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.2" aria-hidden="true"><path d="M3.2 1.4h4L9.4 3.6v7H3.2z"/><path d="M7.2 1.4v2.2H9.4"/></svg>';
const DIFF_SVG = '<svg width="11" height="11" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.2" aria-hidden="true"><path d="M2 1.8h3v8.4H2zM7 1.8h3v8.4H7z"/></svg>';

function render(apps, total) {
  const rows = el("rows");
  const hasApps = apps.length > 0;
  el("empty").style.display = hasApps ? "none" : "block";
  if (!hasApps) {
    el("emptyTitle").textContent = state.query || state.status ? "No matches" : "No applications yet";
    el("emptyHint").textContent = state.query || state.status
      ? "Try a different search or status filter."
      : "Tailor a resume and it'll show up here.";
  }
  el("count").textContent = total ? `${total} result${total === 1 ? "" : "s"}` : "0 results";

  // Build rows as an HTML string, then attach listeners to the result.
  rows.innerHTML = apps
    .map((a) => {
      const st = a.status || "applied";
      return `
      <tr>
        <td class="date" data-label="Date">${escapeHtml(a.date)}</td>
        <td class="cell-company" data-label="Company"><div class="company">${escapeHtml(a.company)}</div><span class="company-sub">${escapeHtml(a.mode)}</span></td>
        <td class="role cell-role" data-label="Role">${escapeHtml(a.role)}</td>
        <td data-label="Signal">${atsCell(a.ats_score)}</td>
        <td data-label="Runtime">${runtimeCell(a)}</td>
        <td data-label="Status"><select class="status-select ${statusClass(st)}" data-id="${a.id}" name="status" aria-label="Status">
          <option value="applied" ${st === "applied" ? "selected" : ""}>applied</option>
          <option value="interview" ${st === "interview" ? "selected" : ""}>interview</option>
          <option value="offered" ${st === "offered" ? "selected" : ""}>offered</option>
        </select></td>
        <td data-label="Artifacts"><div class="artifacts">
          <a class="artifact" href="${API_BASE}/api/resume/${a.id}" target="_blank" title="Open PDF in a new tab">${PDF_SVG}PDF</a>
          <button class="artifact" data-diff="${a.id}" data-file="${escapeHtml(a.filename)}" data-company="${escapeHtml(a.company)}" data-role="${escapeHtml(a.role)}" title="Render the diff report">${DIFF_SVG}Diff</button>
        </div></td>
      </tr>`;
    })
    .join("");

  rows.querySelectorAll(".status-select").forEach((sel) => {
    const original = sel.value;
    sel.addEventListener("change", async () => {
      const next = sel.value;
      try {
        const res = await fetch(`${API_BASE}/api/history/${sel.dataset.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status: next }),
        });
        if (!res.ok) throw new Error("save failed");
        sel.className = `status-select ${statusClass(next)}`;
        loadStats(); // pipeline overview reflects the new status
      } catch (err) {
        // A failed save must NOT appear successful: revert the select and say so.
        sel.value = original;
        sel.className = `status-select ${statusClass(original)}`;
        showToast("Couldn't update status — the local engine may be offline.", true);
      }
    });
  });

  rows.querySelectorAll("button[data-diff]").forEach((btn) => {
    btn.addEventListener("click", () => openDiff(btn));
  });

  // Pagination
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  el("pageInfo").textContent = `Page ${state.page + 1} / ${pages}`;
  el("prev").disabled = state.page === 0;
  el("next").disabled = state.page + 1 >= pages;
}

let toastTimer;
function showToast(msg, isError = false) {
  const toast = el("toast");
  toast.textContent = msg;
  toast.classList.toggle("error", isError);
  toast.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("show"), 2600);
}

// ---------------------------------------------------------------------------
// Diff modal — render the fetched diff.md as styled HTML.
// ---------------------------------------------------------------------------
async function openDiff(btn) {
  const id = btn.dataset.diff;
  const modal = el("diffModal");
  const content = el("diffContent");
  const diffFile = btn.dataset.file ? btn.dataset.file.replace(/\.pdf$/i, "") + ".diff.md" : "diff.md";
  content.className = "diff-scroll";
  content.innerHTML = `<p class="dlg-load">Loading diff report…</p>`;
  modal.classList.add("open");

  el("dlgTitle").textContent = "Diff report";
  el("dlgSub").textContent = btn.dataset.company
    ? `${btn.dataset.company} · ${btn.dataset.role}`
    : diffFile;
  el("dlgRaw").href = `${API_BASE}/api/resume/${id}/diff`;

  try {
    const res = await fetch(`${API_BASE}/api/resume/${id}/diff`);
    if (!res.ok) throw new Error("diff unavailable");
    const text = await res.text();
    content.innerHTML = renderMarkdown(text);
    content.classList.add("rendered");
  } catch (err) {
    content.innerHTML = `<p class="dlg-error">Couldn't load the diff report for this application.</p>`;
  }
}

function closeDiff() {
  el("diffModal").classList.remove("open");
}

el("diffModal").addEventListener("click", (e) => {
  if (e.target === el("diffModal")) closeDiff();
});
el("dlgClose").addEventListener("click", closeDiff);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeDiff();
});

function statusClass(status) {
  return status === "interview" ? "interview" : status === "offered" ? "offered" : "applied";
}

// ---------------------------------------------------------------------------
// Toolbar: search (debounced), status pills, refresh, pagination
// ---------------------------------------------------------------------------
let searchTimer;
el("search").addEventListener("input", (e) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    state.query = e.target.value.trim();
    state.page = 0;
    load();
  }, 300);
});

el("pills").addEventListener("click", (e) => {
  const pill = e.target.closest(".pill");
  if (!pill) return;
  state.status = pill.dataset.status;
  state.page = 0;
  el("pills").querySelectorAll(".pill").forEach((p) => p.classList.toggle("active", p === pill));
  load();
});

el("refresh").addEventListener("click", () => load());

el("prev").addEventListener("click", () => {
  if (state.page > 0) { state.page -= 1; load(); }
});
el("next").addEventListener("click", (e) => {
  if (!e.target.disabled) { state.page += 1; load(); }
});

load();
