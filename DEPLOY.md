# RxDx — Launch guide

This folder is a ready-to-push repository. The **frontend** (`index.html` + PWA
files) goes on **GitHub Pages** (free, static, installable, offline). The
optional **backend** (`backend/`) enables shared, cross-user learning + insights.

```
index.html            the RxDx app (7 tabs)
manifest.json sw.js icon.svg   PWA (installable / offline)
.nojekyll             tells Pages to serve files as-is
.github/workflows/deploy-pages.yml   auto-deploys to Pages on push
backend/              FastAPI learning service (+ Dockerfile, render.yaml, export_onnx.py)
```

---

## 1) Put it on GitHub Pages (frontend)

1. Create a new GitHub repository (e.g. `rxdx`).
2. Upload **everything in this folder** to the repo root (drag-and-drop in the
   GitHub web UI, or `git push`).
3. Repo → **Settings → Pages → Build and deployment → Source: GitHub Actions**.
4. The included workflow deploys automatically. Your app will be live at:
   `https://<username>.github.io/<repo>/`
5. Open it on a phone/desktop → browser menu → **Install** (works offline after).

> Works fully standalone with **local (per-device) learning** — no backend needed.

## 2) Backend for shared learning (optional but recommended)

The backend aggregates `(complaint → ICD)` picks, documentation-quality scores,
weekly trend and learned synonyms across all users. **No patient data is stored.**

**Deploy on Render (free):**
1. Push this repo (it includes `backend/render.yaml` + `Dockerfile`).
2. Render → **New → Blueprint** → select your repo → it reads `backend/render.yaml`.
3. After deploy you get a URL like `https://rxdx-backend.onrender.com`.

(Or `docker build -t rxdx backend/ && docker run -p 8000:8000 rxdx`, or any host.)

**Then connect the frontend:** in `index.html` find:

```js
var LEARN_CFG={syncUrl:""};
```
set it to your backend URL:
```js
var LEARN_CFG={syncUrl:"https://rxdx-backend.onrender.com"};
```
Commit → Insights, trend, quality and learned synonyms become shared.

## 3) In-browser AI disease NER (optional)

Runs a real disease-detection model **in the browser** (no backend), via
Transformers.js.

1. Export an OpenMed disease model to ONNX and host it (e.g. on Hugging Face):
   ```bash
   pip install "optimum[exporters]" onnxruntime transformers
   python backend/export_onnx.py OpenMed/OpenMed-NER-DiseaseDetect-SuperClinical-434M rxdx-disease-onnx
   # push rxdx-disease-onnx to a Hugging Face repo, e.g. <you>/rxdx-disease-onnx
   ```
2. In `index.html` set:
   ```js
   var AI_MODEL="<you>/rxdx-disease-onnx";
   ```
3. In the **Note → Codes** tab, tick **“AI model”** to use it (falls back to the
   dictionary if unavailable). First run downloads the model, then it’s cached.

---

## Safety / privacy
- Runs on-device; clinical text stays in the browser unless you enable the backend.
- Backend stores only anonymous aggregates (complaint→code, phrase→code, quality
  score, timestamp) — no identifiers, no free text, no PHI.
- All extraction / coding / scores / prompts are **aids for a clinician to verify** —
  not diagnostic or decision-making tools.

---

## Access codes & roles (v1.5)

The app now opens with a **passcode screen**. Three roles, each with its own code —
set them near the top of the main script in `index.html`:

```js
var ROLE_CODES={doctor:"doctor2024",admin:"admin2024",it:"it2024"};
```

| Code you give | Role | Sees |
|---|---|---|
| doctor code | **Doctor** | clinical tabs: Drug, ICD‑10, Note→Codes, History Builder, Structured, Calculators |
| admin code | **Management** | Insights dashboard (stats, trends, coding integrity) |
| it code | **IT** | Insights + **IT Review** — approve/reject learning proposals before they reflect |

**Link control (revocable, only via you):** nobody can use the tool without a code
from you. **Change the codes → the old ones stop working** = access revoked instantly.

## How the learning review works
- A doctor picks a working diagnosis → it helps that doctor **immediately (local)**, and
  is sent to the backend as a **proposal** (`/propose`).
- Proposals wait in **IT Review**. When IT clicks **Approve**, the learning is applied and
  starts reflecting in Insights and for other users. **Reject** discards it.

## Recommended hosting — Cloudflare
- **Frontend → Cloudflare Pages** (free, fast, custom domain; you own the project & link).
  Connect the repo, build output = root. Add **Cloudflare Access** (email allowlist) if you
  want an extra login layer on top of the passcode.
- **Backend →** either keep the included FastAPI (`backend/`) on a small container
  (Render/Fly) with Cloudflare in front, **or** port it to a **Cloudflare Worker + D1**
  for a fully-Cloudflare setup (ask and I'll generate the Worker version).
- Then set `LEARN_CFG.syncUrl` in `index.html` to your backend URL.
