// Flash Resume extension popup controller.

const API_BASE = "http://127.0.0.1:13450";

document.addEventListener("DOMContentLoaded", async () => {
  const statusBadge = document.getElementById("statusBadge");
  const statusText = document.getElementById("statusText");
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
    if (persist) chrome.storage.local.set({ fr_mode: normalizedMode });
  };

  chrome.storage.local.get("fr_mode", (data) => {
    applyModeTheme(data.fr_mode);
  });

  modeOptions.forEach((option) => {
    option.addEventListener("click", () => {
      applyModeTheme(option.dataset.mode, true);
    });
  });

  const toggleGuide = () => {
    guideView.style.display = guideView.style.display === "block" ? "none" : "block";
    chrome.storage.local.set({ fr_onboarded: true });
  };

  document.getElementById("guideToggle").addEventListener("click", toggleGuide);
  document.getElementById("reopenGuide").addEventListener("click", toggleGuide);

  chrome.storage.local.get("fr_onboarded", (data) => {
    if (!data.fr_onboarded) guideView.style.display = "block";
  });

  try {
    const res = await fetch(`${API_BASE}/api/status`);
    if (!res.ok) throw new Error("Server error");

    const data = await res.json();
    statusBadge.classList.remove("offline");
    const providerTag = data.provider === "groq" ? "⚡" : "🎯";
    statusText.textContent = data.candidate_name
      ? `${data.candidate_name} ${providerTag} (Ready)`
      : `Engine Ready ${providerTag}`;
    offlineNotice.style.display = "none";

    const hasActiveKey = data.provider === "groq" ? data.has_groq_key : data.has_api_key;
    if (!hasActiveKey) setupCard.style.display = "block";
  } catch (err) {
    statusBadge.classList.add("offline");
    statusText.textContent = "Offline";
    offlineNotice.style.display = "block";
    setupCard.style.display = "block";
  }
});

// Render recent applications (a tiny ?limit=5 request so the popup stays snappy).
// The card is always visible — it shows an empty state until the first tailor.
function loadHistoryPreview() {
  const historyCard = document.getElementById("historyCard");
  historyCard.style.display = "block";
  try {
    fetch(`${API_BASE}/api/history?limit=5`)
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        const apps = data?.applications;
        if (apps?.length) {
          displayHistory(apps);
        } else {
          document.getElementById("historyList").innerHTML =
            `<div style="padding:10px 0;font-size:11.5px;color:var(--text-muted);">
               No applications yet — tailor a resume and it'll show up here.
             </div>`;
        }
      })
      .catch(() => {
        document.getElementById("historyList").innerHTML =
          `<div style="padding:10px 0;font-size:11.5px;color:var(--text-muted);">
             Engine offline — history unavailable right now.
           </div>`;
      });
  } catch (err) {
    // ignore — history is a nice-to-have
  }
}

function displayHistory(apps) {
  const list = document.getElementById("historyList");
  list.innerHTML = apps
    .map(
      (app) => `
      <div style="padding:8px 0;border-bottom:1px solid var(--card-border);
        display:flex;justify-content:space-between;align-items:center;gap:8px;">
        <div style="flex:1;min-width:0;">
          <div style="font-weight:600;font-size:11.5px;color:var(--text);
            white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${escapeHtml(app.company)}</div>
          <div style="font-size:10px;color:var(--text-muted);margin-top:2px;">
            ${escapeHtml(app.role).slice(0, 30)}${app.role.length > 30 ? "…" : ""} · ${app.date} · ATS ${app.ats_score ?? "?"}%
          </div>
        </div>
        <a class="icon-btn" href="${API_BASE}/api/resume/${app.id}" target="_blank" title="Open PDF">📄</a>
      </div>`
    )
    .join("");
  // The PDF action is a plain `<a target="_blank">` — the browser opens it in a
  // new tab natively. No re-download, no JS needed.
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("viewAllHistory").addEventListener("click", () => {
    chrome.tabs.create({ url: chrome.runtime.getURL("history.html") });
  });
  loadHistoryPreview();
});
