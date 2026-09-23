// Flash Resume Extension Popup Controller

const API_BASE = "http://127.0.0.1:8000";

let currentPdfBase64 = null;
let currentFilename = "Tailored_Resume.pdf";

function openOnboarding() {
  chrome.tabs.create({ url: chrome.runtime.getURL("onboarding.html") });
}

document.addEventListener("DOMContentLoaded", async () => {
  const statusBadge = document.getElementById("statusBadge");
  const statusText = document.getElementById("statusText");
  const offlineNotice = document.getElementById("offlineNotice");
  const setupCard = document.getElementById("setupCard");
  const tailorBtn = document.getElementById("tailorBtn");
  const companyInput = document.getElementById("companyInput");
  const roleInput = document.getElementById("roleInput");
  const jdInput = document.getElementById("jdInput");
  const guideView = document.getElementById("guideView");

  // Onboarding entry points
  document.getElementById("openOnboardingBtn").addEventListener("click", openOnboarding);
  document.getElementById("openSetupLink").addEventListener("click", openOnboarding);

  // How-to guide toggle
  const toggleGuide = () => {
    guideView.style.display = guideView.style.display === "block" ? "none" : "block";
  };
  document.getElementById("guideToggle").addEventListener("click", toggleGuide);
  document.getElementById("reopenGuide").addEventListener("click", toggleGuide);

  // First-run: show setup card and auto-open guide until onboarded
  chrome.storage.local.get("fr_onboarded", (data) => {
    if (!data.fr_onboarded) {
      setupCard.style.display = "block";
      guideView.style.display = "block";
    }
  });

  // 1. Check Server Status
  let engineOnline = false;
  try {
    const res = await fetch(`${API_BASE}/api/status`);
    if (res.ok) {
      const data = await res.json();
      engineOnline = true;
      statusBadge.classList.remove("offline");
      statusText.textContent = data.candidate_name ? `${data.candidate_name} (Ready)` : "Engine Ready";
      offlineNotice.style.display = "none";
      if (!data.has_api_key) {
        setupCard.style.display = "block";
      }
    } else {
      throw new Error("Server error");
    }
  } catch (err) {
    statusBadge.classList.add("offline");
    statusText.textContent = "Offline";
    offlineNotice.style.display = "block";
    tailorBtn.disabled = true;
    setupCard.style.display = "block";
  }

  // 2. Extract Job from Active Tab
  // Content script responds first; if the tab was opened before the extension
  // (re)loaded, the content script is not injected, so fall back to
  // chrome.scripting.executeScript which always works on the active tab.
  const fillForm = (response) => {
    if (!response) return;
    if (response.company && !companyInput.value) {
      companyInput.value = response.company;
    }
    if (response.title && !roleInput.value) {
      roleInput.value = response.title;
    }
    if (response.jd && !jdInput.value) {
      jdInput.value = response.jd;
    }
  };

  const extractViaScripting = async (tabId) => {
    try {
      const results = await chrome.scripting.executeScript({
        target: { tabId },
        func: extractJobDetailsInPage,
      });
      if (results && results[0] && results[0].result) {
        fillForm(results[0].result);
      }
    } catch (e) {
      console.warn("Scripting extraction failed:", e);
    }
  };

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tab && tab.id) {
      chrome.tabs.sendMessage(tab.id, { action: "extract_job" }, (response) => {
        if (chrome.runtime.lastError || !response) {
          extractViaScripting(tab.id);
          return;
        }
        fillForm(response);
      });
    }
  } catch (e) {
    console.warn("Could not query active tab:", e);
  }

  // 3. Tailor Action
  tailorBtn.addEventListener("click", async () => {
    const jd = jdInput.value.trim();
    if (!jd) {
      alert("Please provide or paste a Job Description.");
      return;
    }

    const btnSpinner = document.getElementById("btnSpinner");
    const btnText = document.getElementById("btnText");
    tailorBtn.disabled = true;
    btnSpinner.style.display = "block";
    btnText.textContent = "Tailoring...";

    try {
      const resp = await fetch(`${API_BASE}/api/tailor`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          jd: jd,
          company: companyInput.value.trim() || undefined,
          role: roleInput.value.trim() || undefined,
        }),
      });

      if (!resp.ok) {
        const errorData = await resp.json();
        throw new Error(errorData.detail || "Tailoring failed");
      }

      const result = await resp.json();
      currentPdfBase64 = result.pdf_base64;
      currentFilename = `${result.company}_${result.role}.pdf`.replace(/\s+/g, "_");

      // Switch to results view
      document.getElementById("tailorForm").style.display = "none";
      guideView.style.display = "none";
      const resultView = document.getElementById("resultView");
      resultView.style.display = "block";

      document.getElementById("resCompanyRole").textContent = `${result.company} — ${result.role}`;
      document.getElementById("resScore").textContent = `${result.ats_match_score}%`;

      const chipContainer = document.getElementById("chipContainer");
      chipContainer.innerHTML = "";
      (result.matched_keywords || []).forEach((kw) => {
        const span = document.createElement("span");
        span.className = "chip";
        span.textContent = kw;
        chipContainer.appendChild(span);
      });
    } catch (error) {
      alert("Error: " + error.message);
    } finally {
      tailorBtn.disabled = !engineOnline;
      btnSpinner.style.display = "none";
      btnText.textContent = "⚡ Tailor 1-Page Resume";
    }
  });

  // 4. Download PDF Action
  document.getElementById("downloadBtn").addEventListener("click", () => {
    if (!currentPdfBase64) return;
    const blob = b64toBlob(currentPdfBase64, "application/pdf");
    const url = URL.createObjectURL(blob);
    chrome.downloads.download({
      url: url,
      filename: currentFilename,
      saveAs: true,
    });
  });

  // 5. Reset Action
  document.getElementById("resetBtn").addEventListener("click", () => {
    document.getElementById("resultView").style.display = "none";
    document.getElementById("tailorForm").style.display = "block";
  });
});

// Self-contained extractor injected via chrome.scripting when the content
// script is not present (e.g. tabs opened before the extension was loaded).
function extractJobDetailsInPage() {
  const url = window.location.href;
  let title = "";
  let company = "";
  let jd = "";

  if (url.includes("linkedin.com")) {
    title = document.querySelector(".job-details-jobs-unified-top-card__job-title, .topcard__title, h1")?.innerText?.trim() || "";
    company = document.querySelector(".job-details-jobs-unified-top-card__company-name, .topcard__org-name-link, .jobs-unified-top-card__company-name")?.innerText?.trim() || "";
    jd = document.querySelector("#job-details, .jobs-description__content, .description__text")?.innerText?.trim() || "";
  } else if (url.includes("indeed.com")) {
    title = document.querySelector(".jobsearch-JobInfoHeader-title, h1")?.innerText?.trim() || "";
    company = document.querySelector("[data-company-name='true'], .jobsearch-InlineCompanyRating-companyHeader")?.innerText?.trim() || "";
    jd = document.querySelector("#jobDescriptionText, .jobsearch-jobDescriptionText")?.innerText?.trim() || "";
  } else if (url.includes("greenhouse.io") || url.includes("lever.co")) {
    title = document.querySelector(".app-title, .posting-headline h2, h1")?.innerText?.trim() || "";
    company = document.querySelector(".company-name, .main-header-logo, .posting-headline")?.innerText?.trim() || "";
    jd = document.querySelector("#content, .section-wrapper, .posting-description")?.innerText?.trim() || "";
  }

  const selectedText = window.getSelection().toString().trim();
  if (selectedText.length > 50) {
    jd = selectedText;
  }

  if (!jd) {
    const mainEl = document.querySelector("article, main, .job-description, .description");
    if (mainEl) {
      jd = mainEl.innerText.trim();
    }
  }

  return { title, company, jd };
}

function b64toBlob(b64Data, contentType = "", sliceSize = 512) {
  const byteCharacters = atob(b64Data);
  const byteArrays = [];

  for (let offset = 0; offset < byteCharacters.length; offset += sliceSize) {
    const slice = byteCharacters.slice(offset, offset + sliceSize);
    const byteNumbers = new Array(slice.length);
    for (let i = 0; i < slice.length; i++) {
      byteNumbers[i] = slice.charCodeAt(i);
    }
    const byteArray = new Uint8Array(byteNumbers);
    byteArrays.push(byteArray);
  }

  return new Blob(byteArrays, { type: contentType });
}