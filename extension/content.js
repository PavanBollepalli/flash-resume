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
    title = document.querySelector(".job-details-jobs-unified-top-card__job-title, .topcard__title, h1")?.innerText?.trim() || "";
    company = document.querySelector(".job-details-jobs-unified-top-card__company-name, .topcard__org-name-link, .jobs-unified-top-card__company-name")?.innerText?.trim() || "";
    jd = document.querySelector("#job-details, .jobs-description__content, .description__text")?.innerText?.trim() || "";
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
    jd = document.querySelector("#content, .section-wrapper, .posting-description")?.innerText?.trim() || "";
  }

  // Fallbacks: If user highlighted text, use the selection
  const selectedText = window.getSelection().toString().trim();
  if (selectedText.length > 50) {
    jd = selectedText;
  }

  // Generic fallback: grab largest text block or article/main
  if (!jd) {
    const mainEl = document.querySelector("article, main, .job-description, .description");
    if (mainEl) {
      jd = mainEl.innerText.trim();
    }
  }

  return { title, company, jd };
}

// ---------------------------------------------------------------------------
// One-Tap Floating Action Button (FAB)
// Injects a small ⚡ button in the page corner. Tapping it extracts the JD and
// POSTs directly to the local server, turning the resume-tailoring flow into a
// single tap. Hosted in a closed Shadow DOM so site CSS can't leak in, and the
// FAB's own styles can't leak out.
// ---------------------------------------------------------------------------
const FAB_HOST_ID = "flash-resume-fab-host";

function fabVisible() {
  // Show the FAB only on job-looking content: either a site-specific JD block
  // is present, or the user has selected a chunk of text.
  const details = extractJobDetails();
  if (details.jd && details.jd.length > 150) return true;
  const selectedText = window.getSelection().toString().trim();
  return selectedText.length > 50;
}

function setFabState(host, state, message) {
  if (!host) return;
  const btn = host.shadowRoot.querySelector("#fabBtn");
  const toast = host.shadowRoot.querySelector("#fabToast");
  if (btn) {
    btn.dataset.state = state;
    btn.disabled = state === "busy";
    btn.textContent = state === "busy" ? "⏳" : "⚡";
  }
  if (toast) {
    toast.dataset.kind = state === "error" ? "error" : state === "success" ? "success" : "idle";
    if (message) {
      toast.textContent = message;
      toast.style.opacity = "1";
      clearTimeout(host._toastTimer);
      host._toastTimer = setTimeout(() => { toast.style.opacity = "0"; }, 4000);
    }
  }
}

function injectFab() {
  // Skip if already injected.
  if (document.getElementById(FAB_HOST_ID)) return;

  const host = document.createElement("div");
  host.id = FAB_HOST_ID;
  const shadow = host.attachShadow({ mode: "closed" });
  shadow.innerHTML = `
    <style>
      :host { all: initial; }
      * { box-sizing: border-box; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
      #fabBtn {
        position: fixed;
        right: 18px;
        bottom: 18px;
        z-index: 2147483647;
        width: 52px;
        height: 52px;
        border-radius: 50%;
        border: none;
        cursor: pointer;
        background: linear-gradient(135deg, #f97316, #ea580c);
        color: #fff;
        font-size: 24px;
        line-height: 1;
        box-shadow: 0 4px 14px rgba(234, 88, 12, 0.45);
        display: flex;
        align-items: center;
        justify-content: center;
        transition: transform .15s ease;
      }
      #fabBtn:hover { transform: scale(1.08); }
      #fabBtn:disabled { opacity: .75; cursor: wait; }
      #fabToast {
        position: fixed;
        right: 18px;
        bottom: 80px;
        z-index: 2147483647;
        max-width: 320px;
        padding: 10px 14px;
        border-radius: 10px;
        color: #fff;
        font-size: 13px;
        line-height: 1.4;
        opacity: 0;
        transition: opacity .25s ease;
        pointer-events: none;
      }
      #fabToast[data-kind="success"] { background: #16a34a; }
      #fabToast[data-kind="error"]   { background: #dc2626; }
    </style>
    <button id="fabBtn" title="Flash Resume: tailor for this job">⚡</button>
    <div id="fabToast" data-kind="idle"></div>
  `;
  document.body.appendChild(host);

  // Click handler: extract JD and POST straight to the local server.
  shadow.getElementById("fabBtn").addEventListener("click", async () => {
    const details = extractJobDetails();
    if (!details.jd) {
      setFabState(host, "error", "No job description found on this page.");
      return;
    }
    setFabState(host, "busy");
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
      setFabState(
        host,
        "success",
        `✓ ${result.role} at ${result.company} — ${result.ats_match_score}/100, ${result.page_count} page. Saved to ${result.pdf_path}`
      );
    } catch (e) {
      setFabState(host, "error", `Flash Resume: ${e.message}`);
    } finally {
      // Return the button to idle shortly after success, immediately on error.
      setTimeout(() => {
        if (host.shadowRoot.querySelector("#fabBtn").dataset.state === "busy") {
          setFabState(host, "idle");
        }
      }, 1200);
    }
  });
}

function initFab() {
  // Respect the user's on/off toggle (default on).
  chrome.storage.local.get("fr_fab", (data) => {
    if (data.fr_fab === false) return;
    const maybeInit = () => {
      if (!fabVisible()) return;
      // Only inject once body is present.
      if (!document.body) { setTimeout(maybeInit, 300); return; }
      if (fabVisible()) injectFab();
    };
    maybeInit();
  });
}

// Also refresh visibility on selection changes (user highlights JD text).
document.addEventListener("selectionchange", () => {
  if (document.getElementById(FAB_HOST_ID)) return;
  initFab();
});

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
