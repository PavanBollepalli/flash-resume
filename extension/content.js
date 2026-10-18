// content.js — Manifest V3 content script.
// Injects the Flash Resume button on EVERY page, always. No detection, no gates.
// Selecting the JD and clicking the button POSTs it to the local companion
// daemon.

(() => {
  console.log("[Flash Resume] content.js running on", location.hostname);
  const API_BASE = "http://127.0.0.1:13450";
  const FAB_ID = "fr-fab";
  const TOAST_ID = "fr-toast";
  const STYLE_ID = "fr-style";
  let resumeMode = "truthful";
  let selectedJDAtClick = "";

  function sanitizeName(s) {
    return String(s || "")
      .replace(/[\\/:*?"<>|]/g, "")
      .trim()
      .replace(/\s+/g, " ")
      .slice(0, 60) || "resume";
  }

  // Always save to Downloads instantly — the guaranteed win. Fires in ~0ms,
  // never blocks the attach attempt.
  function triggerDownload(base64, filename) {
    const dataUrl = `data:application/pdf;base64,${base64}`;
    try {
      chrome.runtime.sendMessage({ type: "FR_DOWNLOAD", dataUrl, filename }, () => {
        if (chrome.runtime.lastError) {
          // Downloads API unavailable (e.g. file:// preview) — PDF still on disk
          // via the server's output dir (Desktop/FlashResume).
        }
      });
    } catch (_) {
      // No extension context — still saved to disk by the server.
    }
  }

  // Single opportunistic attach — one shot, ~500ms, no polling loop.
  // If a file input exists (Workday / Greenhouse / Lever apply page) it wins;
  // otherwise the user already has the file in Downloads.
  function tryAttachOnce(base64, filename) {
    const payload = { type: "FR_SET_FILE", base64, filename };
    const frames = [
      window,
      ...Array.from(document.querySelectorAll("iframe"))
        .map((f) => f.contentWindow)
        .filter(Boolean),
    ];

    return new Promise((resolve) => {
      let done = false;
      const finish = (ok) => {
        if (done) return;
        done = true;
        window.removeEventListener("message", onResult);
        clearTimeout(timer);
        resolve(ok);
      };

      const onResult = (e) => {
        const r = e.data;
        if (!r || r.type !== "FR_SET_FILE_RESULT") return;
        if (r.ok) finish(true);
      };

      window.addEventListener("message", onResult);
      frames.forEach((w) => w.postMessage(payload, "*"));

      const timer = setTimeout(() => finish(false), 500);
    });
  }

  function applyModeTheme(mode) {
    resumeMode = mode === "interview_prep" ? "interview_prep" : "truthful";
    const fab = document.getElementById(FAB_ID);
    if (fab) {
      const interview = resumeMode === "interview_prep";
      fab.classList.toggle("fr-interview-mode", interview);
      // Sliver/star scheme: the FAB inverts to mirror the popup. Truthful = a
      // white sliver with a black star in the middle; Interview = a black
      // sliver with a white star. Colors are driven entirely by the class, so
      // no inline overrides are needed here.
      fab.textContent = "✦";
      fab.title = interview
        ? "Flash Resume — Interview mode"
        : "Flash Resume — Truthful mode";
      fab.setAttribute("aria-label", fab.title);
    }
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
        box-sizing: border-box; padding: 0; margin: 0;
        --fr-bg: #ffffff;      /* sliver — white (truthful) */
        --fr-fg: #0a0a0a;      /* star in the middle — black (truthful) */
        --fr-shadow: rgba(0,0,0,.32);
        background: var(--fr-bg); border: none; cursor: pointer;
        box-shadow: -2px 0 8px var(--fr-shadow);
        font-size: 0; color: var(--fr-fg); display: flex; align-items: center; justify-content: center;
        overflow: hidden; white-space: nowrap;
        transform-origin: right center;
        transition: width .22s ease, border-radius .22s ease, box-shadow .22s ease,
          background .45s ease, color .45s ease, transform .3s ease, opacity .25s ease;
        font-family: system-ui, sans-serif;
      }
      #${FAB_ID}:hover {
        width: 52px; height: 52px; border-radius: 50%;
        font-size: 22px;
        box-shadow: 0 4px 14px var(--fr-shadow);
      }
      #${FAB_ID}.fr-interview-mode {
        --fr-bg: #0a0a0a;      /* sliver — black (interview) */
        --fr-fg: #ffffff;      /* star — white (interview) */
        --fr-shadow: rgba(0,0,0,.45);
        box-shadow: -2px 0 8px rgba(0,0,0,.45);
      }
      #${FAB_ID}.fr-interview-mode:hover {
        box-shadow: 0 4px 14px rgba(0,0,0,.5);
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

  // The floating button intentionally uses an explicit selection. This avoids
  // accidentally sending navigation, recommendations, or unrelated page text.
  function extractJobDescription() {
    const sel = selectedJDAtClick || window.getSelection()?.toString().trim();
    return sel && sel.length >= 50 ? sel.slice(0, 12000) : "";
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
      showToast("Select the job description first, then click the Flash Resume button on the right edge.", true);
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
      const stamp = new Date().toISOString().slice(0, 16).replace(/[T:]/g, "-");
      const filename = `${stamp}_${sanitizeName(data.company)}_${sanitizeName(data.role)}.pdf`;

      if (!data.pdf_base64) {
        showToast(`✅ ATS ${score}% · ${pages}p · PDF ready (${data.pdf_path || "Desktop/FlashResume"})`);
        return;
      }

      // Downloads is the guaranteed win — fire immediately so the user never waits.
      // Attach runs in parallel as a single opportunistic shot; no 9s polling loop.
      triggerDownload(data.pdf_base64, filename);
      showToast("📥 PDF saved to Downloads — trying to attach…");
      const attached = await tryAttachOnce(data.pdf_base64, filename);
      showToast(
        attached
          ? `✅ ATS ${score}% · ${pages}p · PDF attached + saved to Downloads ✓`
          : `✅ ATS ${score}% · ${pages}p · PDF saved to Downloads — attach it`,
      );
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
    btn.textContent = "✦";
    btn.type = "button";
    btn.title = "Select the job description, then click Flash Resume";
    btn.setAttribute("aria-label", btn.title);
    btn.addEventListener("mousedown", () => {
      selectedJDAtClick = window.getSelection()?.toString().trim() || "";
    });
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