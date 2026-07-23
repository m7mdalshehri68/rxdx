"""RxDx learning backend.

Online, shared, self-learning layer for RxDx.html plus optional model endpoints.

Endpoints
---------
GET  /health
GET  /learn                         -> {presentation: {CODE: count}}
POST /learn {presentation, code, weight?, quality?}
GET  /insights?top=8                -> totals, top_presentations, top_codes, covered,
                                       trend (last 7d vs prior 7d), avg_quality
POST /quality {presentation, score}
POST /synonym {phrase, code}
GET  /synonyms?min_count=1          -> [{phrase, code, count}]
POST /ner {text}                    -> disease entities   (needs RXDX_NER_MODEL)
POST /extract {text, labels?}       -> zero-shot entities (needs RXDX_EXTRACT_MODEL)

Privacy: stores only (complaint -> code) counts, learned phrases (no patient
identifiers), documentation-quality scores, and timestamps. No PHI.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from contextlib import closing
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

DB_PATH = os.environ.get("RXDX_DB", "rxdx_learn.db")
NER_MODEL = os.environ.get("RXDX_NER_MODEL", "").strip()
EXTRACT_MODEL = os.environ.get("RXDX_EXTRACT_MODEL", "").strip()
API_KEY = os.environ.get("RXDX_API_KEY", "").strip()
ALLOW_ORIGINS = [o.strip() for o in os.environ.get("RXDX_ALLOW_ORIGINS", "*").split(",") if o.strip()]
WEEK = 7 * 86400
_write_lock = threading.Lock()


def _init_db() -> None:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS learn(
            presentation TEXT NOT NULL, code TEXT NOT NULL, count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(presentation, code))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS events(
            id INTEGER PRIMARY KEY AUTOINCREMENT, presentation TEXT NOT NULL,
            code TEXT NOT NULL, ts REAL NOT NULL,
            clinician_id TEXT, clinician_name TEXT, dept TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS quality(
            id INTEGER PRIMARY KEY AUTOINCREMENT, presentation TEXT NOT NULL,
            score INTEGER NOT NULL, ts REAL NOT NULL,
            level TEXT, clinician_id TEXT, clinician_name TEXT, dept TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS pending(
            presentation TEXT NOT NULL, code TEXT NOT NULL, count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(presentation, code))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS casemix(
            level TEXT PRIMARY KEY, count INTEGER NOT NULL DEFAULT 0)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS synonyms(
            phrase TEXT NOT NULL, code TEXT NOT NULL, count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(phrase, code))""")
        for tbl, cols in (("events", ("clinician_id", "clinician_name", "dept")),
                          ("quality", ("level", "clinician_id", "clinician_name", "dept")),
                          ("pending", ("clinician_id", "clinician_name", "dept"))):
            for col in cols:
                try:
                    conn.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} TEXT")
                except sqlite3.OperationalError:
                    pass
        conn.commit()


_init_db()

app = FastAPI(title="RxDx Learning Backend", version="0.2.0")
app.add_middleware(CORSMiddleware, allow_origins=ALLOW_ORIGINS or ["*"],
                   allow_methods=["GET", "POST", "OPTIONS"], allow_headers=["*"])


class LearnIn(BaseModel):
    presentation: str
    code: str
    weight: int = 1
    quality: Optional[int] = None
    clinician_id: Optional[str] = None
    clinician_name: Optional[str] = None
    dept: Optional[str] = None


class QualityIn(BaseModel):
    presentation: str
    score: int
    level: Optional[str] = None
    clinician_id: Optional[str] = None
    clinician_name: Optional[str] = None
    dept: Optional[str] = None


class SynIn(BaseModel):
    phrase: str
    code: str
    clinician_id: Optional[str] = None
    clinician_name: Optional[str] = None
    dept: Optional[str] = None


class NerIn(BaseModel):
    text: str


class ExtractIn(BaseModel):
    text: str
    labels: Optional[List[str]] = None


def _auth(x_api_key: Optional[str]) -> None:
    """Integration foundation: if RXDX_API_KEY is set, every write must carry
    it as X-Api-Key. Unset = open demo mode."""
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="invalid or missing X-Api-Key")


def _ctx(item) -> tuple:
    cid = (getattr(item, "clinician_id", None) or "").strip()[:64] or None
    cname = (getattr(item, "clinician_name", None) or "").strip()[:120] or None
    dept = (getattr(item, "dept", None) or "").strip()[:80] or None
    return cid, cname, dept


@app.get("/health")
def health() -> Dict[str, Any]:
    return {"status": "ok", "ner": bool(NER_MODEL), "extract": bool(EXTRACT_MODEL)}


@app.get("/learn")
def get_learn() -> Dict[str, Dict[str, int]]:
    out: Dict[str, Dict[str, int]] = {}
    with closing(sqlite3.connect(DB_PATH)) as conn:
        for p, c, n in conn.execute("SELECT presentation, code, count FROM learn"):
            out.setdefault(p, {})[c] = n
    return out


@app.post("/learn")
def post_learn(item: LearnIn, x_api_key: Optional[str] = Header(None)) -> Dict[str, Any]:
    _auth(x_api_key)
    p = (item.presentation or "").strip()[:200]
    c = (item.code or "").strip().upper()[:20]
    if not p or not c:
        return {"ok": False, "error": "presentation and code are required"}
    w = max(1, min(100, int(item.weight or 1)))
    now = time.time()
    with _write_lock, closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute("""INSERT INTO learn(presentation, code, count) VALUES(?,?,?)
                        ON CONFLICT(presentation, code) DO UPDATE SET count=count+?""", (p, c, w, w))
        cid, cname, dept = _ctx(item)
        conn.execute("INSERT INTO events(presentation, code, ts, clinician_id, clinician_name, dept) VALUES(?,?,?,?,?,?)", (p, c, now, cid, cname, dept))
        if item.quality is not None:
            conn.execute("INSERT INTO quality(presentation, score, ts) VALUES(?,?,?)",
                         (p, max(0, min(100, int(item.quality))), now))
        conn.commit()
    return {"ok": True}


@app.post("/quality")
def post_quality(item: QualityIn, x_api_key: Optional[str] = Header(None)) -> Dict[str, Any]:
    _auth(x_api_key)
    p = (item.presentation or "").strip()[:200]
    if not p:
        return {"ok": False, "error": "presentation required"}
    with _write_lock, closing(sqlite3.connect(DB_PATH)) as conn:
        lv = (item.level or "").strip().upper()[:1]
        cid, cname, dept = _ctx(item)
        conn.execute("INSERT INTO quality(presentation, score, ts, level, clinician_id, clinician_name, dept) VALUES(?,?,?,?,?,?,?)",
                     (p, max(0, min(100, int(item.score))), time.time(), lv if lv in ("R","A","G") else None, cid, cname, dept))
        if lv in ("R", "A", "G"):
            conn.execute("""INSERT INTO casemix(level, count) VALUES(?,1)
                            ON CONFLICT(level) DO UPDATE SET count=count+1""", (lv,))
        conn.commit()
    return {"ok": True}


@app.post("/synonym")
def post_synonym(item: SynIn, x_api_key: Optional[str] = Header(None)) -> Dict[str, Any]:
    _auth(x_api_key)
    ph = (item.phrase or "").strip().lower()[:120]
    c = (item.code or "").strip().upper()[:20]
    if not ph or not c:
        return {"ok": False, "error": "phrase and code are required"}
    with _write_lock, closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute("""INSERT INTO synonyms(phrase, code, count) VALUES(?,?,1)
                        ON CONFLICT(phrase, code) DO UPDATE SET count=count+1""", (ph, c))
        conn.commit()
    return {"ok": True}


@app.get("/synonyms")
def get_synonyms(min_count: int = 1) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    with closing(sqlite3.connect(DB_PATH)) as conn:
        for ph, c, n in conn.execute(
            "SELECT phrase, code, count FROM synonyms WHERE count>=? ORDER BY count DESC",
            (max(1, int(min_count)),)):
            out.append({"phrase": ph, "code": c, "count": n})
    return out


@app.get("/clinicians")
def clinicians() -> List[Dict[str, Any]]:
    """Per-clinician follow-up rollup. Rows exist only when the facility system
    passes clinician identity — demo usage stays anonymous."""
    out: Dict[str, Dict[str, Any]] = {}
    with closing(sqlite3.connect(DB_PATH)) as conn:
        for cid, cname, dept, ts in conn.execute(
                "SELECT clinician_id, clinician_name, dept, ts FROM events WHERE clinician_id IS NOT NULL"):
            r = out.setdefault(cid, {"id": cid, "name": cname, "dept": dept, "picks": 0,
                                     "notes": 0, "quality_sum": 0, "casemix": {}, "last_ts": 0})
            r["picks"] += 1
            if cname: r["name"] = cname
            if dept: r["dept"] = dept
            r["last_ts"] = max(r["last_ts"], ts)
        for cid, cname, dept, score, level, ts in conn.execute(
                "SELECT clinician_id, clinician_name, dept, score, level, ts FROM quality WHERE clinician_id IS NOT NULL"):
            r = out.setdefault(cid, {"id": cid, "name": cname, "dept": dept, "picks": 0,
                                     "notes": 0, "quality_sum": 0, "casemix": {}, "last_ts": 0})
            r["notes"] += 1
            r["quality_sum"] += score
            if level: r["casemix"][level] = r["casemix"].get(level, 0) + 1
            if cname: r["name"] = cname
            if dept: r["dept"] = dept
            r["last_ts"] = max(r["last_ts"], ts)
    res = []
    for r in out.values():
        res.append({"id": r["id"], "name": r["name"], "dept": r["dept"], "picks": r["picks"],
                    "notes": r["notes"],
                    "avg_quality": round(r["quality_sum"] / r["notes"], 1) if r["notes"] else None,
                    "casemix": r["casemix"],
                    "last_seen": time.strftime("%d %b %H:%M", time.localtime(r["last_ts"])) if r["last_ts"] else None})
    res.sort(key=lambda x: -(x["picks"] + x["notes"]))
    return res


@app.get("/insights")
def insights(top: int = 8) -> Dict[str, Any]:
    top = max(1, min(50, int(top)))
    picks = 0
    code_count: Dict[str, int] = {}
    pres_total: Dict[str, int] = {}
    cur: Dict[str, int] = {}
    prev: Dict[str, int] = {}
    now = time.time()
    with closing(sqlite3.connect(DB_PATH)) as conn:
        for p, c, n in conn.execute("SELECT presentation, code, count FROM learn"):
            pres_total[p] = pres_total.get(p, 0) + n
            code_count[c] = code_count.get(c, 0) + n
            picks += n
        for p, ts in conn.execute("SELECT presentation, ts FROM events"):
            if ts >= now - WEEK:
                cur[p] = cur.get(p, 0) + 1
            elif ts >= now - 2 * WEEK:
                prev[p] = prev.get(p, 0) + 1
        qrow = conn.execute("SELECT AVG(score), COUNT(*) FROM quality").fetchone()
        casemix = {lv: n for lv, n in conn.execute("SELECT level, count FROM casemix")}

    def _topn(d: Dict[str, int]) -> List[Dict[str, Any]]:
        return sorted(({"key": k, "count": v} for k, v in d.items()), key=lambda x: -x["count"])[:top]

    trend = [{"presentation": p, "last7": cur[p], "prior7": prev.get(p, 0),
              "delta": cur[p] - prev.get(p, 0)} for p in cur]
    trend.sort(key=lambda x: -x["delta"])

    return {
        "totals": {"picks": picks, "presentations": len(pres_total), "codes": len(code_count)},
        "top_presentations": [{"presentation": x["key"], "count": x["count"]} for x in _topn(pres_total)],
        "top_codes": [{"code": x["key"], "count": x["count"]} for x in _topn(code_count)],
        "covered": sorted(pres_total.keys()),
        "trend": trend[:top],
        "avg_quality": {"avg": round(qrow[0], 1) if qrow[0] is not None else None, "n": qrow[1]},
        "casemix": casemix,
    }


class ReviewIn(BaseModel):
    presentation: str
    code: str
    action: str  # "approve" or "reject"


@app.post("/propose")
def propose(item: LearnIn, x_api_key: Optional[str] = Header(None)) -> Dict[str, Any]:
    _auth(x_api_key)
    """A doctor's working-diagnosis pick — queued for IT review before it counts."""
    p = (item.presentation or "").strip()[:200]
    c = (item.code or "").strip().upper()[:20]
    if not p or not c:
        return {"ok": False, "error": "presentation and code are required"}
    w = max(1, min(100, int(item.weight or 1)))
    with _write_lock, closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute("""INSERT INTO pending(presentation, code, count) VALUES(?,?,?)
                        ON CONFLICT(presentation, code) DO UPDATE SET count=count+?""", (p, c, w, w))
        conn.commit()
    return {"ok": True, "status": "pending"}


@app.get("/pending")
def get_pending(top: int = 100) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    with closing(sqlite3.connect(DB_PATH)) as conn:
        for p, c, n in conn.execute(
            "SELECT presentation, code, count FROM pending ORDER BY count DESC LIMIT ?",
            (max(1, min(1000, int(top))),)):
            out.append({"presentation": p, "code": c, "count": n})
    return out


@app.post("/review")
def review(item: ReviewIn, x_api_key: Optional[str] = Header(None)) -> Dict[str, Any]:
    _auth(x_api_key)
    """IT approves (apply to shared learning) or rejects a pending proposal."""
    p = (item.presentation or "").strip()[:200]
    c = (item.code or "").strip().upper()[:20]
    act = (item.action or "").strip().lower()
    if not p or not c or act not in ("approve", "reject"):
        return {"ok": False, "error": "presentation, code and action(approve|reject) required"}
    now = time.time()
    with _write_lock, closing(sqlite3.connect(DB_PATH)) as conn:
        row = conn.execute("SELECT count FROM pending WHERE presentation=? AND code=?", (p, c)).fetchone()
        n = row[0] if row else 0
        if act == "approve" and n:
            conn.execute("""INSERT INTO learn(presentation, code, count) VALUES(?,?,?)
                            ON CONFLICT(presentation, code) DO UPDATE SET count=count+?""", (p, c, n, n))
            conn.execute("INSERT INTO events(presentation, code, ts) VALUES(?,?,?)", (p, c, now))
        conn.execute("DELETE FROM pending WHERE presentation=? AND code=?", (p, c))
        conn.commit()
    return {"ok": True, "action": act}


# --- optional model endpoints (need a model attached; not required for learning) ---
_MODELS: Dict[str, Any] = {}


def _load_token_model(model_id: str):
    if model_id not in _MODELS:
        from transformers import AutoModelForTokenClassification, AutoTokenizer, pipeline
        tok = AutoTokenizer.from_pretrained(model_id)
        mdl = AutoModelForTokenClassification.from_pretrained(model_id)
        _MODELS[model_id] = pipeline("token-classification", model=mdl, tokenizer=tok,
                                     aggregation_strategy="simple")
    return _MODELS[model_id]


def _entities(pipe, text: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for e in pipe(text):
        out.append({"text": e.get("word"), "label": e.get("entity_group"),
                    "start": int(e["start"]) if e.get("start") is not None else None,
                    "end": int(e["end"]) if e.get("end") is not None else None,
                    "confidence": float(e["score"]) if e.get("score") is not None else None})
    return out


@app.post("/ner")
def ner(item: NerIn) -> Dict[str, Any]:
    if not NER_MODEL:
        return {"enabled": False, "entities": [], "note": "Set RXDX_NER_MODEL to enable disease NER."}
    return {"enabled": True, "entities": _entities(_load_token_model(NER_MODEL), item.text)}


@app.post("/extract")
def extract(item: ExtractIn) -> Dict[str, Any]:
    """Broader entity extraction (symptoms/procedures/etc.).

    Point RXDX_EXTRACT_MODEL at an OpenMed token-classification model (or a
    zero-shot NER model). This is the hook for symptom/procedure extraction.
    """
    if not EXTRACT_MODEL:
        return {"enabled": False, "entities": [],
                "note": "Set RXDX_EXTRACT_MODEL (e.g. an OpenMed zero-shot/NER model) to enable."}
    return {"enabled": True, "entities": _entities(_load_token_model(EXTRACT_MODEL), item.text)}
