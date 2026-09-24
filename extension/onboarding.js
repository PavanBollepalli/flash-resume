// Flash Resume Extension - Onboarding Controller

const API_BASE = "http://127.0.0.1:13450";
const TOTAL_STEPS = 6;

let currentStep = 1;
let selectedProvider = "gemini";

document.addEventListener("DOMContentLoaded", () => {
  renderProgress();

  // MV3 CSP forbids inline onclick handlers, so bind everything here.
  document.getElementById("btnStart").addEventListener("click", () => goTo(2));
  document.getElementById("checkServerBtn").addEventListener("click", checkServer);
  document.getElementById("btnBack2").addEventListener("click", () => goTo(1));
  document.getElementById("btnNext2").addEventListener("click", () => goTo(3));

  // Step 3: provider choice
  document.getElementById("providerGemini").addEventListener("click", () => selectProvider("gemini"));
  document.getElementById("providerGroq").addEventListener("click", () => selectProvider("groq"));
  document.getElementById("btnBack3").addEventListener("click", () => goTo(2));
  document.getElementById("btnNext3").addEventListener("click", saveProviderAndAdvance);

  // Step 4: API key — autosaved on Continue
  document.getElementById("btnBack4key").addEventListener("click", () => goTo(3));
  document.getElementById("btnNext4key").addEventListener("click", saveKeyAndAdvance);

  // Step 5: save location — autosaved on Continue
  document.getElementById("btnBack5").addEventListener("click", () => goTo(4));
  document.getElementById("btnNext5").addEventListener("click", saveDirAndAdvance);

  // Step 6: checklist
  document.getElementById("btnRefresh").addEventListener("click", refreshChecklist);
  document.getElementById("btnFinish").addEventListener("click", finish);

  hydrateProviderFromEngine();
});

function goTo(step) {
  currentStep = step;
  for (let i = 1; i <= TOTAL_STEPS; i++) {
    document.getElementById(`step${i}`).classList.toggle("visible", i === step);
  }
  renderProgress();
  if (step === 4) configureKeyStep();
  if (step === 5) prefillOutputDir();
  if (step === 6) refreshChecklist();
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

function selectProvider(provider) {
  selectedProvider = provider;
  document.getElementById("providerGemini").classList.toggle("selected", provider === "gemini");
  document.getElementById("providerGroq").classList.toggle("selected", provider === "groq");
}

async function hydrateProviderFromEngine() {
  // Reflect the provider already stored on the engine, if it's running.
  try {
    const res = await fetch(`${API_BASE}/api/status`);
    if (!res.ok) return;
    const data = await res.json();
    if (data.provider === "groq" || data.provider === "gemini") {
      selectProvider(data.provider);
    }
  } catch (e) {
    // engine offline: keep local default
  }
}

async function saveProviderAndAdvance() {
  // Persist the provider choice, then move to the key step. Don't block
  // navigation hard on failure — the key save will surface engine issues.
  try {
    await fetch(`${API_BASE}/api/config/provider`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider: selectedProvider }),
    });
  } catch (e) {
    // engine offline: choice is applied on the next save attempt
  }
  goTo(4);
}

function configureKeyStep() {
  const title = document.getElementById("keyStepTitle");
  const lede = document.getElementById("keyStepLede");
  const label = document.getElementById("keyStepLabel");
  const input = document.getElementById("apiKeyInput");
  const hint = document.getElementById("keyStepHint");

  if (selectedProvider === "groq") {
    title.textContent = "Add your Groq API key";
    lede.textContent = "Groq serves OpenAI gpt-oss-20b at blazing speed on dedicated LPUs. Paste your key below — it's saved to your local Flash Resume config only.";
    label.textContent = "Groq API Key";
    input.placeholder = "gsk_…";
    hint.innerHTML = 'Get a free key at <a href="https://console.groq.com/keys" target="_blank" rel="noopener">console.groq.com/keys</a>. If you already set <code>GROQ_API_KEY</code> in your environment, you can skip this step.';
  } else {
    title.textContent = "Add your Gemini API key";
    lede.textContent = "Gemini 3.5 Flash Lite gives the best keyword-matching quality for ATS tailoring. Paste your key below — it's saved to your local Flash Resume config only.";
    label.textContent = "Gemini API Key";
    input.placeholder = "AIza…";
    hint.innerHTML = 'Get a free key at <a href="https://aistudio.google.com/" target="_blank" rel="noopener">aistudio.google.com</a> → Get API key. If you already set <code>GEMINI_API_KEY</code> in your environment, you can skip this step.';
  }
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

async function saveKeyAndAdvance() {
  const input = document.getElementById("apiKeyInput");
  const pill = document.getElementById("keyPill");
  const nextBtn = document.getElementById("btnNext4key");
  const key = input.value.trim();

  // Empty key = user relies on env var, skip persisting and advance.
  if (!key) {
    setPill(pill, "ok", "✓ Skipping — using env var");
    goTo(5);
    return;
  }

  const endpoint = selectedProvider === "groq" ? "/api/config/groq-key" : "/api/config/key";

  nextBtn.disabled = true;
  nextBtn.textContent = "Saving…";
  setPill(pill, "wait", "Saving…");
  try {
    const resp = await fetch(`${API_BASE}${endpoint}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ api_key: key }),
    });
    if (!resp.ok) {
      const data = await resp.json().catch(() => ({}));
      throw new Error(data.detail || "Could not save key");
    }
    setPill(pill, "ok", "✓ Key saved");
    chrome.storage.local.set({ fr_onboarded: true });
    goTo(5);
  } catch (e) {
    setPill(pill, "bad", "✗ " + e.message + " (is the engine running?)");
  } finally {
    nextBtn.disabled = false;
    nextBtn.textContent = "Continue →";
  }
}

async function prefillOutputDir() {
  const input = document.getElementById("outputDirInput");
  if (input.value) return;
  try {
    const res = await fetch(`${API_BASE}/api/status`);
    if (!res.ok) throw new Error("bad status");
    const data = await res.json();
    if (data.output_dir) input.value = data.output_dir;
  } catch (e) {
    // engine offline: leave the field for manual entry
  }
}

async function saveDirAndAdvance() {
  const input = document.getElementById("outputDirInput");
  const pill = document.getElementById("dirPill");
  const nextBtn = document.getElementById("btnNext5");
  const dir = input.value.trim();

  if (!dir) {
    setPill(pill, "bad", "✗ Enter a folder path");
    return;
  }

  nextBtn.disabled = true;
  nextBtn.textContent = "Saving…";
  setPill(pill, "wait", "Saving…");
  try {
    const resp = await fetch(`${API_BASE}/api/config/output-dir`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ output_dir: dir }),
    });
    if (!resp.ok) throw new Error("Could not save folder");
    const data = await resp.json();
    input.value = data.output_dir;
    setPill(pill, "ok", "✓ Saved: " + data.output_dir);
    goTo(6);
  } catch (e) {
    setPill(pill, "bad", "✗ " + e.message + " (is the engine running?)");
  } finally {
    nextBtn.disabled = false;
    nextBtn.textContent = "Continue →";
  }
}

async function refreshChecklist() {
  const chkServer = document.getElementById("chkServer");
  const chkKey = document.getElementById("chkKey");
  const chkResume = document.getElementById("chkResume");
  const chkDir = document.getElementById("chkDir");
  const chkKeyLabel = document.getElementById("chkKeyLabel");

  chkServer.classList.remove("ok");
  chkKey.classList.remove("ok");
  chkResume.classList.remove("ok");
  chkDir.classList.remove("ok");

  try {
    const res = await fetch(`${API_BASE}/api/status`);
    if (!res.ok) throw new Error("bad status");
    const data = await res.json();
    chkServer.classList.add("ok");
    const isGroq = data.provider === "groq";
    chkKeyLabel.textContent = isGroq
      ? "Groq API key configured (⚡ speed mode)"
      : "Gemini API key configured (🎯 quality mode)";
    if (isGroq ? data.has_groq_key : data.has_api_key) chkKey.classList.add("ok");
    if (data.master_resume_configured) chkResume.classList.add("ok");
    if (data.output_dir) chkDir.classList.add("ok");
  } catch (e) {
    // engine offline: leave all unchecked
  }
}

function finish() {
  chrome.storage.local.set({ fr_onboarded: true });
  window.close();
}