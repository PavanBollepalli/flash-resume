// content.js — Manifest V3 content script.
// Injects the ⚡ sliver on EVERY page, always. No detection, no gates.
// One tap extracts the JD (known selectors → selection → main content →
// body) and POSTs it to the local companion daemon.

(() => {
  console.log("[Flash Resume] content.js running on", location.hostname);
  const API_BASE = "http://127.0.0.1:13450";
  const FAB_ID = "fr-fab";
  const TOAST_ID = "fr-toast";
  const STYLE_ID = "fr-style";
  let resumeMode = "truthful";

  function applyModeTheme(mode) {
    resumeMode = mode === "interview_prep" ? "interview_prep" : "truthful";
    const fab = document.getElementById(FAB_ID);
    if (fab) fab.classList.toggle("fr-interview-mode", resumeMode === "interview_prep");
  }

  chrome.storage.local.get("fr_mode", (data) => {
    applyModeTheme(data.fr_mode);
  });
  chrome.storage.onChanged.addListener((changes, areaName) => {
    if (areaName === "local" && changes.fr_mode) {
      applyModeTheme(changes.fr_mode.newValue);
    }
  });

  function injectStyles() {
    if (document.getElementById(STYLE_ID)) return;
    const style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = `
      /* Anchor the right edge (transform-origin: right center) so the button
         grows leftward on hover and never moves out from under the cursor —
         otherwise it flickers (expand → cursor leaves → collapse → loop). */
      #${FAB_ID} {
        position: fixed; bottom: 24px; right: 0; z-index: 2147483647;
        width: 6px; height: 48px; border-radius: 3px 0 0 3px;
        background: #3b82f6; border: none; cursor: pointer;
        box-shadow: -2px 0 8px rgba(59,130,246,.35);
        font-size: 0; color: #fff; display: flex; align-items: center; justify-content: center;
        overflow: hidden; white-space: nowrap;
        transform-origin: right center;
        transition: width .22s ease, border-radius .22s ease, box-shadow .22s ease;
        font-family: system-ui, sans-serif;
      }
      #${FAB_ID}:hover {
        width: 52px; height: 52px; border-radius: 50%;
        font-size: 22px;
        box-shadow: 0 4px 14px rgba(59,130,246,.45);
      }
      #${FAB_ID}.fr-interview-mode {
        background: #d92d20;
        box-shadow: -2px 0 8px rgba(180,35,24,.35);
      }
      #${FAB_ID}.fr-interview-mode:hover {
        box-shadow: 0 4px 14px rgba(180,35,24,.45);
      }
      #${FAB_ID}.fr-working { pointer-events: none; opacity: .6; animation: fr-pulse 1s infinite; }
      @keyframes fr-pulse { 0%,100% { transform: scale(1); } 50% { transform: scale(1.08); } }
      #${TOAST_ID} {
        position: fixed; bottom: 90px; right: 24px; z-index: 2147483647;
        background: #1e293b; color: #f1f5f9; padding: 12px 18px; border-radius: 10px;
        font: 13px/1.5 system-ui, sans-serif; max-width: 340px;
        box-shadow: 0 4px 16px rgba(0,0,0,.3); opacity: 0; transform: translateY(8px);
        transition: opacity .25s, transform .25s; pointer-events: none;
      }
      #${TOAST_ID}.fr-show { opacity: 1; transform: translateY(0); }
      #${TOAST_ID}.fr-error { background: #7f1d1d; }
    `;
    document.head.appendChild(style);
  }

  function showToast(msg, isError = false) {
    let toast = document.getElementById(TOAST_ID);
    if (!toast) {
      toast = document.createElement("div");
      toast.id = TOAST_ID;
      document.body.appendChild(toast);
    }
    toast.className = isError ? "fr-error" : "";
    toast.textContent = msg;
    requestAnimationFrame(() => toast.classList.add("fr-show"));
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => toast.classList.remove("fr-show"), 6000);
  }

  // JD extraction with a deep fallback chain so it works on any website:
  // known board selectors → selection → article/main → bounded body text.
  function extractJobDescription() {
    const known = document.querySelector(
      // LinkedIn / Indeed / Greenhouse / Lever
      ".jobs-description__content, .show-more-less-html__markup, .description__text, " +
      "#jobDescriptionText, .jobsearch-JobComponent-description, .app_body, #job_app, " +
      ".posting-description, [itemprop='description'], " +
      // Generic ATS markup (Workday, iCIMS, SmartRecruiters, Ashby, custom…)
      "[class*='job-description'], [class*='jobDescription'], " +
      "[class*='posting-description'], [id*='jobDescription'], [id*='job-description']"
    );
    if (known && known.innerText.trim().length > 80) return known.innerText;

    const sel = window.getSelection()?.toString().trim();
    if (sel && sel.length > 80) return sel;

    const main = document.querySelector("article, main, [role='main']");
    if (main && main.innerText.trim().length > 200) return main.innerText.slice(0, 12000);

    return document.body.innerText.slice(0, 12000);
  }

  function inferCompanyRole() {
    let company = "", role = "";
    company =
      document.querySelector(".jobs-unified-top-link__company-name, .topcard__org-name-link, [data-testid='inlineHeader-companyName'], .company-name, h1.company")?.innerText.trim() || "";
    role =
      document.querySelector(".jobs-unified-top-link__job-title, .topcard__title, h1.t-24, #jobsearch-JobInfoHeader-title, .app-title, h1.title")?.innerText.trim() || "";
    return { company, role };
  }

  async function tailor() {
    const jd = extractJobDescription();
    if (!jd || jd.trim().length < 80) {
      showToast("⚠️ No job text found on this page. Select the JD and tap ⚡ again.", true);
      return;
    }
    const { company, role } = inferCompanyRole();
    const fab = document.getElementById(FAB_ID);
    fab?.classList.add("fr-working");
    showToast("⚡ Tailoring resume…");

    try {
      const res = await fetch(`${API_BASE}/api/tailor`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          jd,
          company: company || null,
          role: role || null,
          mode: resumeMode,
        }),
      });
      const data = await res.json();
      if (!res.ok || data.error || data.success === false) {
        showToast(`❌ ${data.error || data.detail || "Tailoring failed — is 'fs serve' running?"}`, true);
        return;
      }
      const score = data.ats_match_score ?? "?";
      const pages = data.page_count ?? 1;
      showToast(`✅ ATS ${score}% · ${pages} page${pages > 1 ? "s" : ""} · Saved: ${data.pdf_path || "PDF ready"}`);
    } catch (err) {
      showToast(`❌ Can't reach the local engine. Run 'fs serve' or restart via 'fs autostart enable'.`, true);
    } finally {
      fab?.classList.remove("fr-working");
    }
  }

  function createFab() {
    if (document.getElementById(FAB_ID)) return;
    injectStyles();
    const btn = document.createElement("button");
    btn.id = FAB_ID;
    btn.textContent = "⚡";
    btn.title = "Flash Resume — Tailor resume to this job";
    btn.addEventListener("click", tailor);
    document.body.appendChild(btn);
    applyModeTheme(resumeMode);
  }

  // Always visible, everywhere — no detection, no gates.
  const tryInject = () => {
    if (document.body) createFab();
    else setTimeout(tryInject, 200);
  };
  tryInject();
})();