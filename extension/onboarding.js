// Flash Resume Extension - Onboarding Controller

const API_BASE = "http://127.0.0.1:8000";
const TOTAL_STEPS = 4;

let currentStep = 1;

document.addEventListener("DOMContentLoaded", () => {
  renderProgress();

  // MV3 CSP forbids inline onclick handlers, so bind everything here.
  document.getElementById("btnStart").addEventListener("click", () => goTo(2));
  document.getElementById("checkServerBtn").addEventListener("click", checkServer);
  document.getElementById("btnBack2").addEventListener("click", () => goTo(1));
  document.getElementById("btnNext2").addEventListener("click", () => goTo(3));
  document.getElementById("saveKeyBtn").addEventListener("click", saveKey);
  document.getElementById("btnBack3").addEventListener("click", () => goTo(2));
  document.getElementById("btnNext3").addEventListener("click", () => goTo(4));
  document.getElementById("btnRefresh").addEventListener("click", refreshChecklist);
  document.getElementById("btnFinish").addEventListener("click", finish);
});

function goTo(step) {
  currentStep = step;
  for (let i = 1; i <= TOTAL_STEPS; i++) {
    document.getElementById(`step${i}`).classList.toggle("visible", i === step);
  }
  renderProgress();
  if (step === 4) refreshChecklist();
}

function renderProgress() {
  const bar = document.getElementById("progressBar");
  bar.innerHTML = "";
  for (let i = 1; i <= TOTAL_STEPS; i++) {
    const dot = document.createElement("div");
    dot.className = "step-dot";
    if (i < currentStep) dot.className += " done";
    else if (i === currentStep) dot.className += " active";
    dot.textContent = i < currentStep ? "✓" : i;
    bar.appendChild(dot);
    if (i < TOTAL_STEPS) {
      const line = document.createElement("div");
      line.className = "step-line" + (i < currentStep ? " done" : "");
      bar.appendChild(line);
    }
  }
}

function setPill(el, kind, text) {
  el.style.display = "inline-flex";
  el.className = "status-pill " + kind;
  el.textContent = text;
}

async function checkServer() {
  const pill = document.getElementById("serverPill");
  setPill(pill, "wait", "Checking…");
  try {
    const res = await fetch(`${API_BASE}/api/status`);
    if (!res.ok) throw new Error("bad status");
    setPill(pill, "ok", "✓ Engine is online");
  } catch (e) {
    setPill(pill, "bad", "✗ Engine offline — run fs serve first");
  }
}

async function saveKey() {
  const input = document.getElementById("apiKeyInput");
  const pill = document.getElementById("keyPill");
  const key = input.value.trim();

  if (!key) {
    setPill(pill, "bad", "✗ Enter an API key or skip if using env var");
    return;
  }

  setPill(pill, "wait", "Saving…");
  try {
    const resp = await fetch(`${API_BASE}/api/config/key`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ api_key: key }),
    });
    if (!resp.ok) {
      const data = await resp.json().catch(() => ({}));
      throw new Error(data.detail || "Could not save key");
    }
    setPill(pill, "ok", "✓ Key saved to local config");
    chrome.storage.local.set({ fr_onboarded: true });
  } catch (e) {
    setPill(pill, "bad", "✗ " + e.message + " (is the engine running?)");
  }
}

async function refreshChecklist() {
  const chkServer = document.getElementById("chkServer");
  const chkKey = document.getElementById("chkKey");
  const chkResume = document.getElementById("chkResume");

  chkServer.classList.remove("ok");
  chkKey.classList.remove("ok");
  chkResume.classList.remove("ok");

  try {
    const res = await fetch(`${API_BASE}/api/status`);
    if (!res.ok) throw new Error("bad status");
    const data = await res.json();
    chkServer.classList.add("ok");
    if (data.has_api_key) chkKey.classList.add("ok");
    if (data.master_resume_configured) chkResume.classList.add("ok");
  } catch (e) {
    // engine offline: leave all unchecked
  }
}

function finish() {
  chrome.storage.local.set({ fr_onboarded: true });
  window.close();
}