# RxDx Learning Backend

A tiny FastAPI service that gives **RxDx.html** an online, shared, self-learning
layer — and an optional disease-NER endpoint powered by **OpenMed**.

- **Frontend** (`RxDx.html`) → host as a static file on **GitHub Pages**.
- **Backend** (this service) → host anywhere that runs a container / Python
  (Render, Railway, Fly.io, a VPS, or your hospital server).

The frontend already knows how to talk to it — you just point it at the URL.

---

## What it does

| Endpoint | Method | Purpose |
|---|---|---|
| `/health` | GET | liveness + whether NER is enabled |
| `/learn` | GET | returns aggregated `{presentation: {ICD_CODE: count}}` |
| `/learn` | POST | `{presentation, code, weight?}` → increments a count |
| `/ner` | POST | `{text}` → disease entities (needs a model; off by default) |

**Self-learning:** every time a doctor picks a working diagnosis for a complaint
in RxDx, the frontend POSTs `(presentation → code)`. Over time the most-picked
codes for each complaint rise to the top ("Frequent here"), so selection gets
faster and more accurate **the more the tool is used** — across all users.

**Privacy:** the store keeps *only* complaint→ICD-code counts. **No patient
identifiers, no free text, no PHI.** Safe under PDPL/HIPAA-style rules.

---

## Run locally

```bash
cd rxdx_backend
pip install -r requirements.txt
uvicorn app:app --reload --port 8000
# open http://localhost:8000/health
```

## Enable disease NER (optional)

Point `RXDX_NER_MODEL` at a local model folder (e.g. the OpenMed disease-detect
model you downloaded) or an OpenMed registry key, and install `openmed`:

```bash
pip install openmed
export RXDX_NER_MODEL="/path/to/OpenMed-NER-DiseaseDetect-SuperClinical"   # or: disease_detection_superclinical
uvicorn app:app --port 8000
# POST /ner {"text":"Patient with pneumonia and COPD"} -> disease entities
```

## Deploy (Docker)

```bash
docker build -t rxdx-backend .
docker run -p 8000:8000 -v $PWD/data:/data rxdx-backend
```

On **Render/Railway/Fly.io**: create a new web service from this folder, keep
the default start command (`uvicorn app:app --host 0.0.0.0 --port $PORT`), and
add a persistent volume for the SQLite DB (`RXDX_DB=/data/rxdx_learn.db`).

### Environment variables

| Var | Default | Meaning |
|---|---|---|
| `RXDX_DB` | `rxdx_learn.db` | SQLite path (use a mounted volume in prod) |
| `RXDX_ALLOW_ORIGINS` | `*` | comma-separated CORS origins (set to your Pages URL) |
| `RXDX_NER_MODEL` | *(empty)* | model path/key to enable `/ner` |

---

## Connect the RxDx frontend

Open `RxDx.html`, find this line in the History-Builder script:

```js
var LEARN_CFG={syncUrl:""};
```

Set it to your deployed backend base URL:

```js
var LEARN_CFG={syncUrl:"https://your-backend.onrender.com"};
```

That's it. On load RxDx pulls shared learning (`GET /learn`); on each diagnosis
pick it pushes (`POST /learn`). If the backend is unreachable, RxDx keeps working
offline using its local `localStorage` learning.

---

## Roadmap ideas (backend-powered)

- **Learned synonyms for Note → Codes:** store accepted `note-phrase → code`
  mappings and serve them back so free-text coding improves over time.
- **Differential-diagnosis priors:** seed each complaint with common
  differentials, then let real usage re-rank them.
- **Investigation appropriateness:** add an `/investigations` dataset
  (diagnosis → recommended initial workup) to power a service-justification
  check — kept as an educational aid with clear disclaimers.

---

## v0.2 additions

- `POST /learn` now also accepts `quality` (0–100) and logs a timestamped event.
- `POST /quality {presentation, score}` — record documentation-completeness scores.
- `POST /synonym {phrase, code}` + `GET /synonyms` — learned free-text → code mappings.
- `GET /insights` now also returns `trend` (last 7 days vs prior 7 — surveillance signal) and `avg_quality`.
- `POST /extract {text}` — broader entity extraction (symptoms/procedures); set `RXDX_EXTRACT_MODEL` to an OpenMed NER/zero-shot model. `/ner` stays for disease detection.

All new stores are anonymous (complaint→code, phrase→code, quality score, timestamp) — no patient data.

---

## v0.3 — Practitioner identity & facility-system integration (foundation)

The demo stays fully **anonymous**. When the facility system (HIS/EMR) is ready,
it passes clinician identity and everything becomes trackable per doctor — no
code changes needed in the tool.

### How the HIS passes identity (any one of three)

| Method | How |
|---|---|
| URL | open `index.html?cid=EMP001&cname=Dr%20Ahmed&dept=ER` |
| Host config | set `window.RXDX_HOST_CTX = {id,name,dept}` before the page loads |
| Embedded (iframe) | `iframe.contentWindow.postMessage({type:'rxdx:ctx', id, name, dept}, '*')` |

The identity shows as a chip in the top bar, is kept for the session, and is
attached to every write (`/learn`, `/propose`, `/quality`, `/synonym`).

### New/changed API

- All write bodies accept optional `clinician_id`, `clinician_name`, `dept`.
- `GET /clinicians` → per-doctor rollup: `{id, name, dept, picks, notes, avg_quality, casemix{R,A,G}, last_seen}` — powers the IT **Clinicians** screen.
- **API key (foundation):** set env `RXDX_API_KEY` → every write must send header `X-Api-Key`. Frontend: `LEARN_CFG.apiKey`. Unset = open demo mode.

### Privacy note
Identity is the **clinician's** (employee id/name/department) — still **no patient
data**. For production, pair with the facility's SSO and store ids only.
