// Flash Resume extension popup controller.

const API_BASE = "http://127.0.0.1:13450";

// chrome.* is only available inside the extension context. Guard every call so
// the page still renders (mode switching works) when it's opened on file:// or
// in a preview where the API is absent — it just won't persist or navigate.
function chromeReady() {
  try {
    return typeof chrome !== "undefined" && !!chrome.storage?.local && !!chrome.tabs;
  } catch (_) {
    return false;
  }
}

// The loaded extension build, so "Active" can be paired with what's running.
function extVersion() {
  try {
    return `v${chrome.runtime.getManifest().version}`;
  } catch (_) {
    return "";
  }
}

// Defensive ATS normalisation: never render null/missing as 0, clamp to 0–100.
function normScore(v) {
  if (v == null || v === "") return null;
  const n = Number(v);
  if (Number.isNaN(n)) return null;
  return Math.max(0, Math.min(100, Math.round(n)));
}
function scoreInfo(v) {
  const n = normScore(v);
  if (n == null) return { cls: "na", text: "–" };
  return { cls: n >= 85 ? "hi" : n >= 70 ? "mid" : "lo", text: `${n}%` };
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

const PDF_ICON =
  '<svg width="11" height="11" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.2" aria-hidden="true"><path d="M3.2 1.4h4L9.4 3.6v7H3.2z"/><path d="M7.2 1.4v2.2H9.4"/></svg>';

document.addEventListener("DOMContentLoaded", async () => {
  const statusBadge = document.getElementById("statusBadge");
  const statusText = document.getElementById("statusText");
  const identityName = document.getElementById("identityName");
  const identityVersion = document.getElementById("identityVersion");
  const offlineNotice = document.getElementById("offlineNotice");
  const setupCard = document.getElementById("setupCard");
  const modeInput = document.getElementById("modeInput");
  const modeOptions = [...document.querySelectorAll(".mode-option")];
  const modeDescription = document.getElementById("modeDescription");
  const guideView = document.getElementById("guideView");

  const modeCopy = {
    truthful: "Your resume stays evidence-led while matching the job honestly.",
    interview_prep: "Adds closely related skills as Familiarity — never as production experience.",
  };

  const applyModeTheme = (mode, persist = false) => {
    const interview = mode === "interview_prep";
    const normalizedMode = interview ? "interview_prep" : "truthful";
    modeInput.value = normalizedMode;
    document.body.classList.toggle("interview-mode", interview);
    modeOptions.forEach((option) => {
      const active = option.dataset.mode === normalizedMode;
      option.classList.toggle("active", active);
      option.setAttribute("aria-pressed", String(active));
    });
    modeDescription.textContent = modeCopy[normalizedMode];
    if (persist && chromeReady()) chrome.storage.local.set({ fr_mode: normalizedMode });
  };

  if (chromeReady()) {
    chrome.storage.local.get("fr_mode", (data) => applyModeTheme(data.fr_mode));
  }

  modeOptions.forEach((option) => {
    option.addEventListener("click", () => applyModeTheme(option.dataset.mode, true));
  });

  const toggleGuide = () => {
    guideView.style.display = guideView.style.display === "block" ? "none" : "block";
    if (chromeReady()) chrome.storage.local.set({ fr_onboarded: true });
  };

  const guideToggle = document.getElementById("guideToggle");
  if (guideToggle) guideToggle.addEventListener("click", toggleGuide);
  document.getElementById("reopenGuide").addEventListener("click", toggleGuide);

  if (chromeReady()) {
    chrome.storage.local.get("fr_onboarded", (data) => {
      if (!data.fr_onboarded) guideView.style.display = "block";
    });
  }

  // The badge answers one question — is this working? — so it states
  // ACTIVE / INACTIVE instead of echoing the candidate's name. Whose resume is
  // loaded and which build is running go on the identity line beneath it.
  const setStatus = (active) => {
    statusBadge.classList.toggle("offline", !active);
    statusText.textContent = active ? "Active" : "Inactive";
    const ver = extVersion();
    identityVersion.textContent = ver;
    identityVersion.parentElement.classList.toggle("no-version", !ver);
  };

  try {
    const res = await fetch(`${API_BASE}/api/status`);
    if (!res.ok) throw new Error("Server error");

    const data = await res.json();
    setStatus(true);
    identityName.textContent = data.candidate_name || "Engine ready";
    offlineNotice.style.display = "none";

    const hasActiveKey = data.provider === "groq" ? data.has_groq_key : data.has_api_key;
    if (!hasActiveKey) setupCard.style.display = "block";
    if (!data.master_resume_configured) identityName.textContent = "No master resume yet";
  } catch (err) {
    setStatus(false);
    identityName.textContent = "Engine not running";
    offlineNotice.style.display = "block";
    setupCard.style.display = "block";
  }
});

// Render recent applications (a tiny ?limit=5 request so the popup stays snappy).
// The card is always visible — it shows an empty state until the first tailor.
function loadHistoryPreview() {
  const historyCard = document.getElementById("historyCard");
  const historyList = document.getElementById("historyList");
  historyCard.style.display = "block";
  const showNote = (text) => {
    historyList.innerHTML = `<div class="list-note">${escapeHtml(text)}</div>`;
  };
  try {
    fetch(`${API_BASE}/api/history?limit=5`)
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        const apps = data?.applications;
        if (apps?.length) displayHistory(apps);
        else showNote("No applications yet — tailor a resume and it'll show up here.");
      })
      .catch(() => showNote("Engine offline — history unavailable right now."));
  } catch (err) {
    // ignore — history is a nice-to-have
  }
}

function displayHistory(apps) {
  const list = document.getElementById("historyList");
  list.innerHTML = apps
    .map((app) => {
      const { cls, text } = scoreInfo(app.ats_score);
      return `
      <div class="app-row">
        <div class="app-main">
          <div class="app-company">${escapeHtml(app.company)}</div>
          <div class="app-meta">${escapeHtml(app.role)} · <span class="mono">${escapeHtml(app.date)}</span></div>
        </div>
        <span class="app-score ${cls}" title="ATS signal ${text === "–" ? "not scored" : text}">${text}</span>
        <a class="app-pdf" href="${API_BASE}/api/resume/${app.id}" target="_blank" title="Open PDF">${PDF_ICON}PDF</a>
      </div>`;
    })
    .join("");
  // The PDF action is a plain `<a target="_blank">` — the browser opens it in a
  // new tab natively. No re-download, no JS needed.
}

document.addEventListener("DOMContentLoaded", () => {
  const viewAll = document.getElementById("viewAllHistory");
  if (viewAll) {
    viewAll.addEventListener("click", () => {
      if (chromeReady()) chrome.tabs.create({ url: chrome.runtime.getURL("history.html") });
    });
  }
  loadHistoryPreview();
});
