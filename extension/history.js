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
  if (score == null) {
    return `<div class="ats na"><div class="top"><span class="num">–</span></div><div class="meter"><i style="width:0%"></i></div></div>`;
  }
  const cls = score >= 85 ? "hi" : score >= 70 ? "mid" : "lo";
  return `<div class="ats ${cls}"><div class="top"><span class="num">${score}</span><span class="pct">%</span></div><div class="meter"><i style="width:${Math.min(100, score)}%"></i></div></div>`;
}

// ---------------------------------------------------------------------------
// Pipeline overview (Total · Interview · Offers · Avg Signal)
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
  const avg = s.avg_ats;
  setVal(".i-ats .val", avg == null ? "–" : avg);
  const signal = el("stats").querySelector(".i-ats .signal i");
  if (signal) signal.style.width = `${Math.min(100, avg ?? 0)}%`;
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
    .map(
      (a) => `
      <tr>
        <td class="date">${escapeHtml(a.date)}</td>
        <td>
          <div class="company">${escapeHtml(a.company)}</div>
          <small>${escapeHtml(a.mode)}</small>
        </td>
        <td class="role">${escapeHtml(a.role)}</td>
        <td>${atsCell(a.ats_score)}</td>
        <td>
          <select class="status-select ${statusClass(a.status)}" data-id="${a.id}" name="status">
            <option value="applied" ${a.status === "applied" ? "selected" : ""}>applied</option>
            <option value="interview" ${a.status === "interview" ? "selected" : ""}>interview</option>
            <option value="offered" ${a.status === "offered" ? "selected" : ""}>offered</option>
          </select>
        </td>
        <td>
          <div class="actions">
            <a class="btn" href="${API_BASE}/api/resume/${a.id}" target="_blank" title="Open PDF in a new tab"><span class="ico">📄</span>PDF</a>
            <button class="btn ghost" data-diff="${a.id}" data-file="${escapeHtml(a.filename)}" data-company="${escapeHtml(a.company)}" data-role="${escapeHtml(a.role)}" title="Render the diff report"><span class="ico">📝</span>Diff</button>
          </div>
        </td>
      </tr>`
    )
    .join("");

  rows.querySelectorAll(".status-select").forEach((sel) => {
    sel.addEventListener("change", async (e) => {
      await fetch(`${API_BASE}/api/history/${sel.dataset.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: sel.value }),
      });
      sel.className = `status-select ${statusClass(sel.value)}`;
      loadStats(); // pipeline overview reflects the new status
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