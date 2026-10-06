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
