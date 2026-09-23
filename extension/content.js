// Flash Resume - Intelligent Job Description Content Extractor

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

// Listen for messages from popup
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === "extract_job") {
    const details = extractJobDetails();
    sendResponse(details);
  }
  return true;
});

