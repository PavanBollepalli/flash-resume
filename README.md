# ⚡ Flash Resume

> **Fast, ATS-targeted resume tailoring and layout-verified 1-page PDF generation.**

Stop the manual **3-tab shuffle** (Job Board ↔ ChatGPT ↔ Overleaf). Full document AI rewrites take 15+ seconds, hallucinate experience, and push single-page resumes onto page 2.

**Flash Resume** solves this by:
1. **Targeted ATS Diffing**: Injects high-density keywords from the job description directly into existing skills and bullets with a **strict word-count budget ($\pm 2$ words)**.
2. **Instant Typst Compilation**: Compiles your resume locally in **< 100 milliseconds** via bundled Typst.
3. **Programmatic Overflow Protection**: Uses `pypdf` to inspect the compiled document and guarantee a **verified single-page fit**.
4. **Choice of AI engine**: Pick **Gemini** for best ATS keyword quality (~15-20s) or **Groq** for near-instant tailoring (**~3s**) — switch any time from the extension setup.
5. **1-Click Browser Extension**: Tailor from LinkedIn, Indeed, or Greenhouse — the verified PDF is saved straight to your chosen folder, no download prompt.

---

## 🚀 Quick Start

### 1. Installation

Clone the repository and install dependencies with [`uv`](https://docs.astral.sh/uv/):

```powershell
# Clone repo
git clone https://github.com/PavanBollepalli/flash-resume.git
cd flash-resume

# Install dependencies
uv sync
```

### 2. Initial Setup (Run Once)

Run the interactive setup wizard to configure your master resume and preferred output folder:

```powershell
uv run fs init
```

*Don't have a JSON resume yet? The wizard can automatically create a starter master resume at `~/.config/flash-resume/master_resume.json` for you.*

### 3. Set Your AI Provider API Key

Pick one — **Gemini** (best quality) or **Groq** (fastest):

```powershell
# Gemini (quality, ~15-20s)
$env:GEMINI_API_KEY="your-gemini-key"   # from https://aistudio.google.com/

# OR Groq (speed, ~3s)
$env:GROQ_API_KEY="your-groq-key"       # from https://console.groq.com/keys
```

You can also enter either key (and choose the provider) right inside the extension's setup flow.

### 4. Verify System Health

```powershell
uv run fs doctor
```

---

## ⚡ Daily Usage

### The Everyday Command: `fs tailor`

When browsing jobs on LinkedIn, Naukri, or Indeed:

1. **Copy the job description** to your clipboard (`Ctrl + C`).
2. Run:

```powershell
uv run fs tailor
```

You can also make clipboard input explicit with `uv run fs tailor --clip`.

*That's it!* Flash Resume:
- Automatically reads the JD from your system clipboard
- Infers target **Company** and **Role**
- Integrates high-impact ATS keywords into relevant bullets and skills
- Compiles the PDF and verifies the 1-page layout
- Saves:
  - `Company_Role.pdf` (Ready to upload)
  - `Company_Role.json` (Structured tailored resume)
  - `Company_Role.diff.md` (Reviewable before/after report)
- Renders a Rich visual terminal diff:

```text
✓ Flash Resume Tailoring Complete
Datadog — Backend Software Engineer
ATS Match Score: 94%  |  Pages: 1 page (Layout Verified)  |  Compile: 72.5ms  |  Total: 1.84s

Integrated ATS Keywords:  FastAPI    PostgreSQL    Docker    Redis 
```

### Custom Overrides (Optional)

```powershell
# Pass a job description file or string directly
uv run fs tailor --jd ./jobs/datadog_swe.txt

# Paste a long job description safely, including quotes and apostrophes
Get-Content ./jobs/joveo.txt | uv run fs tailor --jd -
# Git Bash alternative:
cat ./jobs/joveo.txt | uv run fs tailor --jd -

# Override company or role name
uv run fs tailor --company "Google" --role "Site Reliability Engineer"

# Specify a custom output folder
uv run fs tailor --out ./applications/2026/
```

---

## 🧩 Chrome Extension (1-Click Tailor)

Prefer not to leave your browser? Use the included Chrome Extension companion:

1. In your terminal, start the local companion server:
   ```powershell
   uv run fs serve
   ```
2. In Google Chrome:
   - Navigate to `chrome://extensions/`
   - Enable **Developer mode** (top-right toggle).
   - Click **Load unpacked** and select the `extension/` folder in this repo.
3. Run the included **first-run setup** (click the extension icon → **Setup**): it walks you through checking the engine, picking your AI provider (Gemini or Groq), entering your API key, and choosing a **save folder** — everything **autosaves** as you click **Continue**.
4. Open any job posting on **LinkedIn**, **Indeed**, or **Greenhouse**.
5. Click the **Flash Resume** icon → **"⚡ Tailor 1-Page Resume"**. The company, role, and job description are auto-filled (including from text you've selected on the page). The verified PDF is saved directly to your configured folder and the saved path is shown — no download dialog.

---

## 🏗️ Architecture

```
flash-resume/
├── pyproject.toml               # Package dependencies & scripts
├── README.md                    # Project documentation
├── templates/
│   └── resume.typ               # Modern, ATS-optimized Typst template
├── extension/                   # Manifest V3 Chrome Extension
│   ├── manifest.json
│   ├── popup.html / popup.js    # Tailor UI + saved-path view (no download prompt)
│   ├── onboarding.html / onboarding.js  # 6-step autosaving setup wizard
│   └── content.js
├── src/flash_resume/
│   ├── cli.py                   # Typer CLI (init, tailor, doctor, serve)
│   ├── config.py                # ~/.config/flash-resume/config.json
│   ├── models/
│   │   ├── resume.py            # MasterResume & structured components
│   │   ├── job.py               # JobAnalysis & extracted specs
│   │   └── tailoring.py         # TailorPlan, BulletEdit & TailorResult
│   ├── services/
│   │   ├── compiler.py          # Typst compilation & pypdf page validation
│   │   ├── llm.py               # Gemini Flash structured ATS optimization
│   │   ├── groq_llm.py          # Groq (gpt-oss-20b) fast provider
│   │   ├── tailor.py            # Pipeline orchestrator & diff generator
│   │   └── server.py            # FastAPI companion daemon for Chrome Extension
│   └── utils/
│       └── diff.py              # Rich terminal visualization
├── examples/
│   ├── master_resume.json       # Realistic reference master resume
│   └── job_description.txt      # Sample job description
└── tests/
    ├── test_models.py
    ├── test_compiler.py
    └── test_tailor.py
```

---

## 🛡️ Product Principles

* **100% Factual Truthfulness**: The engine only maps keywords and rephrases bullets that align with existing experience. It never invents companies, degrees, metrics, or domains you haven't touched.
* **Layout-Preserving Word Budgets**: Replacement bullets are strictly constrained to $\pm 2$ words of the original bullet, preventing multi-line overflow.
* **Sub-100ms Local Compilation**: Pure local Typst compilation ensures blazing-fast rendering without waiting for external server queues.

---

## 🧪 Testing

Run automated tests:

```powershell
uv run pytest
```

