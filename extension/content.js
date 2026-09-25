// Flash Resume - Intelligent Job Description Content Extractor + One-Tap FAB

// Base URL of the local companion server.
const API_BASE = "http://127.0.0.1:13450";

function extractJobDetails() {
  const url = window.location.href;
  let title = "";
  let company = "";
  let jd = "";

  // 1. LinkedIn Job Page
  if (url.includes("linkedin.com")) {
    title = document.querySelector(
      ".job-details-jobs-unified-top-card__job-title, .topcard__title, h1, [class*='job-title']"
    )?.innerText?.trim() || "";
    company = document.querySelector(
      ".job-details-jobs-unified-top-card__company-name, .topcard__org-name-link, .jobs-unified-top-card__company-name, [class*='company-name']"
    )?.innerText?.trim() || "";
    jd = document.querySelector(
      "#job-details, .jobs-description__content, .jobs-box__html-content, .description__text, .jobs-description__container, [class*='job-description']"
    )?.innerText?.trim() || "";
  }
  // 2. Indeed Job Page
  else if (url.includes("indeed.com")) {
    title = document.querySelector(".jobsearch-JobInfoHeader-title, h1")?.innerText?.trim() || "";
    company = document.querySelector("[data-company-name='true'], .jobsearch-InlineCompanyRating-companyHeader")?.innerText?.trim() || "";
    jd = document.querySelector("#jobDescriptionText, .jobsearch-jobDescriptionText")?.innerText?.trim() || "";
  }
  // 3. Greenhouse / Lever
  else if (url.includes("greenhouse.io") || url.includes("lever.co")) {
    title = document.querySelector(".app-title, .posting-headline h2, h1")?.innerText?.trim() || "";
    company = document.querySelector(".company-name, .main-header-logo, .posting-headline")?.innerText?.trim() || "";
    jd = document.querySelector("#content, .section-wrapper, .posting-description, .job-description")?.innerText?.trim() || "";
  }

  // Fallbacks: If user highlighted text, use the selection
  const selectedText = window.getSelection().toString().trim();
  if (selectedText.length > 50) {
    jd = selectedText;
  }

  // Generic fallback: grab the largest readable text block in a job-like
  // container (cheap, bounded — no full-page scan that could freeze a busy SPA).
  if (!jd) {
    const sel = document.querySelector(
      "article, main, .job-description, .jobDescriptionText, .description, #job-details, " +
      "[class*='job-description'], [class*='job-posting'], [data-testid*='job']"
    );
    const t = (sel && sel.innerText || "").trim();
    if (t.length > 120) jd = t;
  }

  return { title, company, jd };
}

// ---------------------------------------------------------------------------
// One-Tap Floating Action Button (FAB)
// A small ⚡ button docks in the bottom-right corner of job pages. Tapping it
// extracts the JD, POSTs it to the local server, and shows a live progress /
// result panel right on the page — one tap, no popup. Hosted in a closed
// Shadow DOM so site CSS can't leak in and the FAB's own styles can't leak out.
// ---------------------------------------------------------------------------
const FAB_HOST_ID = "flash-resume-fab-host";

// Renders the button + status panel state. `panelState` is one of:
//   idle | busy | success | error
// NOTE: `shadow` is the root returned by attachShadow({mode:"closed"}) — for a
// closed shadow, host.shadowRoot is null even to the creator, so we must hold
// and pass the shadow reference itself.
function render(shadow, panelState) {
  const btn = shadow.querySelector("#fabBtn");
  const panel = shadow.querySelector("#fabPanel");
  const spinner = shadow.querySelector("#panelSpinner");
  if (!btn) return;

  btn.dataset.state = panelState;
  btn.disabled = panelState === "busy";
  btn.textContent = panelState === "busy" ? "" : "";
  if (panelState !== "busy") {
    // Ensure SVG stays in place (textContent clears children)
    const svg = shadow.getElementById("fabIcon");
    if (!svg) {
      const svgNS = "http://www.w3.org/2000/svg";
      const s = document.createElementNS(svgNS, "svg");
      s.id = "fabIcon";
      s.setAttribute("viewBox", "0 0 24 24");
      s.setAttribute("fill", "none");
      s.setAttribute("stroke", "white");
      s.setAttribute("stroke-width", "1.5");
      s.setAttribute("stroke-linecap", "round");
      s.setAttribute("stroke-linejoin", "round");
      const path = document.createElementNS(svgNS, "path");
      path.setAttribute("d", "M13 2L3 14h9l-1 8 10-12h-9l1-8z");
      s.appendChild(path);
      btn.appendChild(s);
    }
  } else {
    // Clear the SVG during busy state
    const svg = shadow.getElementById("fabIcon");
    if (svg) svg.remove();
  }
  btn.classList.toggle("busy", panelState === "busy");

  // Only the little title spinner rotates — never the body text.
  if (spinner) spinner.style.display = panelState === "busy" ? "inline-block" : "none";

  if (!panel) return;
  panel.classList.toggle("open", panelState !== "idle");
  panel.dataset.state = panelState;
}

function injectFab() {
  if (document.getElementById(FAB_HOST_ID)) return;

  const host = document.createElement("div");
  host.id = FAB_HOST_ID;
  const shadow = host.attachShadow({ mode: "closed" });
  shadow.innerHTML = `
    <style>
      :host { all: initial; }
      * { box-sizing: border-box; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
      .wrap { position: fixed; right: 18px; bottom: 18px; z-index: 2147483647; display: flex; flex-direction: column; align-items: flex-end; gap: 12px; }
      #fabPanel {
        max-width: 340px; min-width: 240px; padding: 14px 16px; border-radius: 14px;
        background: rgba(15, 23, 42, 0.96); color: #e2e8f0; font-size: 13px; line-height: 1.5;
        box-shadow: 0 12px 34px rgba(0,0,0,0.35); border: 1px solid rgba(255,255,255,0.08);
        backdrop-filter: blur(10px); opacity: 0; transform: translateY(8px) scale(0.98);
        transition: opacity .18s ease, transform .18s ease; pointer-events: none;
      }
      #fabPanel.open { opacity: 1; transform: translateY(0) scale(1); pointer-events: auto; }
      #fabPanel[data-state="busy"] { border-color: rgba(59, 130, 246, 0.5); }
      #fabPanel[data-state="success"] { border-color: rgba(34, 197, 94, 0.5); }
      #fabPanel[data-state="error"] { border-color: rgba(239, 68, 68, 0.5); }
      .panel-title { display: flex; align-items: center; gap: 8px; font-weight: 700; font-size: 13px; margin-bottom: 6px; }
      #fabPanel[data-state="busy"] .panel-title { color: #60a5fa; }
      #fabPanel[data-state="success"] .panel-title { color: #4ade80; }
      #fabPanel[data-state="error"] .panel-title { color: #f87171; }
      #fabPanelBody { color: #cbd5e1; font-size: 12.5px; white-space: pre-wrap; word-break: break-word; }
      #fabPanelBody .path { color: #94a3b8; font-size: 11.5px; }
      .spinner {
        width: 14px; height: 14px; flex: none; border-radius: 50%;
        border: 2px solid rgba(59,130,246,0.3); border-top-color: #60a5fa;
        animation: frspin .7s linear infinite;
      }
      @keyframes frspin { to { transform: rotate(360deg); } }
      .close { margin-left: auto; background: none; border: none; color: #94a3b8; cursor: pointer; font-size: 14px; line-height: 1; padding: 0 2px; }
      .close:hover { color: #fff; }
      #fabBtn {
        width: 56px; height: 56px; border-radius: 50%; border: none; cursor: pointer;
        background: linear-gradient(135deg, #3b82f6, #2563eb); color: #fff; font-size: 22px; line-height: 1;
        box-shadow: 0 6px 18px rgba(59, 130, 246, 0.5); display: flex; align-items: center; justify-content: center;
        transition: transform .15s ease, box-shadow .15s ease;
      }
      #fabBtn:hover { transform: scale(1.06); box-shadow: 0 8px 24px rgba(59, 130, 246, 0.6); }
      #fabBtn:disabled { opacity: .8; cursor: wait; }
      #fabBtn svg { width: 26px; height: 26px; display: block; pointer-events: none; }
      #fabBtn.busy svg { display: none; }
      #fabBtn.busy::before { content: ""; width: 20px; height: 20px; border-radius: 50%; border: 3px solid rgba(255,255,255,0.35); border-top-color: #fff; animation: frspin .7s linear infinite; }
    </style>
    <div class="wrap">
      <div id="fabPanel" data-state="idle">
        <div class="panel-title">
          <span id="panelSpinner" class="spinner" style="display:none"></span>
          <span id="fabPanelTitle">Flash Resume</span>
          <button class="close" id="fabClose" title="Dismiss">✕</button>
        </div>
        <div id="fabPanelBody"></div>
      </div>
      <button id="fabBtn" title="Flash Resume: tailor for this job"><svg id="fabIcon" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" xmlns="http://www.w3.org/2000/svg"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg></button>
    </div>
  `;
  document.body.appendChild(host);

  const btn = shadow.getElementById("fabBtn");
  const panelBody = shadow.getElementById("fabPanelBody");
  const panelTitle = shadow.getElementById("fabPanelTitle");
  const close = shadow.getElementById("fabClose");

  close.addEventListener("click", () => render(shadow, "idle"));

  btn.addEventListener("click", async () => {
    if (btn.dataset.state === "busy") return;
    const details = extractJobDetails();
    if (!details.jd) {
      panelTitle.textContent = "No job description found";
      panelBody.textContent = "Select the job description text on the page and tap ⚡ again.";
      render(shadow, "error");
      return;
    }
    // Kick off: loud progress so the tap always visibly does something.
    panelTitle.textContent = "Tailoring your resume…";
    panelBody.textContent = "Detected: " + (details.title || "the job") + " @ " + (details.company || "the company");
    render(shadow, "busy");

    const start = performance.now();
    try {
      const resp = await fetch(`${API_BASE}/api/tailor`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          jd: details.jd,
          company: details.company || undefined,
          role: details.title || undefined,
        }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || "Tailoring failed");
      }
      const result = await resp.json();
      const secs = ((performance.now() - start) / 1000).toFixed(1);
      panelTitle.textContent = `✅ ${result.ats_match_score}/100 ATS · ${result.page_count} page`;
      panelBody.textContent = `Took ${secs}s — saved to:\n${result.pdf_path}`;
      render(shadow, "success");
    } catch (e) {
      panelTitle.textContent = "Tailoring failed";
      panelBody.textContent = `Flash Resume: ${e.message}\nIs the engine running? (Start it with "fs serve")`;
      render(shadow, "error");
    }
  });
}

function initFab() {
  chrome.storage.local.get("fr_fab", (data) => {
    if (data.fr_fab === false) return;
    const maybeInit = () => {
      if (!document.body) { setTimeout(maybeInit, 300); return; }
      injectFab();
    };
    maybeInit();
  });
}

// Listen for messages from popup
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === "extract_job") {
    const details = extractJobDetails();
    sendResponse(details);
  }
  if (request.action === "toggle_fab") {
    chrome.storage.local.set({ fr_fab: request.enabled });
    if (request.enabled) {
      injectFab();
    } else {
      document.getElementById(FAB_HOST_ID)?.remove();
    }
  }
  return true;
});

initFab();
