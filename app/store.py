"""App state: one JSON file built from data/seed. Small, readable, easy to reset.
Swap this module for a database later; the rest of the app only uses these functions."""
import json
import threading
import uuid

import yaml

from . import config

_lock = threading.Lock()
_state: dict | None = None


def _path():
    return config.ROOT / config.settings()["store"]["state_file"]


def _seed() -> dict:
    from . import llm  # local import: llm imports store helpers indirectly

    patients = yaml.safe_load((config.DATA / "seed" / "patients.yaml").read_text(encoding="utf-8"))
    for p in patients:
        for m in p.get("messages", []):
            m["analysis"] = llm.analyse_text(m["text"], allow_ai=False)  # keyword net only at seed time
        p.setdefault("escalations", [])
        p.setdefault("confirmations", [])
    return {"patients": patients, "orders": [], "decisions": [], "audit": [], "drafts": {}}


def state() -> dict:
    global _state
    if _state is None:
        path = _path()
        _state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else _seed()
        if not path.exists():
            save()
    return _state


def save():
    with _lock:
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_state, ensure_ascii=False, indent=1), encoding="utf-8")


def reset():
    global _state
    config.reload()
    _state = _seed()
    save()


def reset_patient(pid: str):
    """Put one patient back to the seed (demo reset from the patient menu). Other patients are untouched."""
    fresh = next(p for p in _seed()["patients"] if p["id"] == pid)
    s = state()
    s["patients"] = [fresh if p["id"] == pid else p for p in s["patients"]]
    for key in ("orders", "decisions"):
        s[key] = [x for x in s[key] if x.get("patient") != pid]
    s["drafts"].pop(pid, None)
    audit("patient", "reset", "Demo data reset from the patient menu", pid)


def patients() -> list:
    return state()["patients"]


def patient(pid: str) -> dict:
    for p in patients():
        if p["id"] == pid:
            return p
    raise KeyError(pid)


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def audit(actor: str, action: str, detail: str, patient_id: str | None = None):
    state()["audit"].insert(0, {"at": config.now().isoformat(sep=" "), "actor": actor, "action": action,
                                "patient": patient_id, "detail": detail})
    save()


def latest_analysis(p: dict) -> dict | None:
    """Analysis of the newest patient message, if any."""
    msgs = sorted(p.get("messages", []), key=lambda m: m["at"])
    return msgs[-1].get("analysis") if msgs else None
