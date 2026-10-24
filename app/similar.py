"""'What happened to similar patients': hard filters first, then note similarity.
Runs in InterSystems IRIS (Vector Search) when store.backend = iris, otherwise locally with the same logic.
It can only add caution: a close note with a bad outcome turns a step into a HOLD, never the reverse."""
import logging
import math
import re
import zlib
from functools import lru_cache
from statistics import median

import yaml

from . import config

log = logging.getLogger("dose-d.similar")


# ---------- embeddings ----------
# char-ngram hashing: works offline for Thai (no word spaces needed). To use a model instead,
# replace embed() and change embedding.dims in settings.yaml (and re-run scripts/seed_iris.py).

def embed(text: str) -> list[float]:
    e = config.settings()["embedding"]
    s = re.sub(r"[\s\W_]+", "", (text or "").lower())
    vec = [0.0] * e["dims"]
    for n in range(e["ngram_min"], e["ngram_max"] + 1):
        for i in range(len(s) - n + 1):
            vec[zlib.crc32(s[i:i + n].encode("utf-8")) % e["dims"]] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [round(v / norm, 6) for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


@lru_cache
def episodes() -> list[dict]:
    rows = yaml.safe_load((config.DATA / "seed" / "episodes.yaml").read_text(encoding="utf-8"))
    for r in rows:
        r["vec"] = embed(r["note"])
    return rows


# ---------- search ----------

def _local(drug, from_dose, to_dose, egfr, k, qvec):
    cfg = config.settings()["similar"]
    rows = []
    for e in episodes():
        if (e["drug"] == drug and e["from"] == from_dose and e["to"] == to_dose
                and abs(e["egfr"] - egfr) <= cfg["egfr_band"] and abs(e["k_before"] - k) <= cfg["k_band"]):
            rows.append({**{x: e[x] for x in ("id", "egfr", "k_before", "k_after", "outcome", "note")},
                         "sim": round(cosine(e["vec"], qvec), 3) if qvec else None})
    return rows


def search(drug: str, from_dose: float, to_dose: float, egfr: float, k: float, query_text: str | None) -> dict:
    cfg = config.settings()["similar"]
    qvec = embed(query_text) if query_text else None
    backend, note = "local", None
    if config.settings()["store"]["backend"] == "iris":
        try:
            from . import iris_db
            rows = iris_db.hybrid_search(drug, from_dose, to_dose, egfr, k, qvec or [0.0] * config.settings()["embedding"]["dims"])
            if not qvec:
                for r in rows:
                    r["sim"] = None
            backend = "iris"
        except Exception as e:  # noqa: BLE001 – show the fallback honestly in the UI
            log.error("IRIS search failed, using local search: %s", e)
            rows, note = _local(drug, from_dose, to_dose, egfr, k, qvec), f"IRIS unavailable ({type(e).__name__}); searched locally"
    else:
        rows = _local(drug, from_dose, to_dose, egfr, k, qvec)

    bad = set(cfg["bad_outcomes"])
    deltas = [round(r["k_after"] - r["k_before"], 2) for r in rows if r["k_after"] is not None]
    result = {
        "backend": backend, "note": note, "n": len(rows), "enough": len(rows) >= cfg["min_cases"],
        "min_cases": cfg["min_cases"], "bad": sum(1 for r in rows if r["outcome"] in bad),
        "lost": sum(1 for r in rows if r["outcome"] == "lost"),
        "k_median": median(deltas) if deltas else None, "k_min": min(deltas) if deltas else None,
        "k_max": max(deltas) if deltas else None,
        "matched_on": config.tr("same drug and step") + f" · eGFR {egfr - cfg['egfr_band']:.0f}–{egfr + cfg['egfr_band']:.0f} · "
                      f"K+ {k - cfg['k_band']:.1f}–{k + cfg['k_band']:.1f}",
        "neighbours": [],
    }
    if qvec:
        result["neighbours"] = sorted(rows, key=lambda r: r["sim"], reverse=True)[: cfg["top_k"]]
    return result


def guard(result: dict) -> dict | None:
    """HOLD if a close note ended badly. Returns the reason, or None."""
    cfg = config.settings()["similar"]
    hits = [r for r in result["neighbours"] if r["sim"] is not None and r["sim"] >= cfg["hold_cutoff"]
            and r["outcome"] in cfg["bad_outcomes"]]
    if not hits:
        return None
    return {"cases": hits, "text": config.tr("{n} similar past patients with closely matching words had a bad outcome "
                                             "after this step").format(n=len(hits))}
