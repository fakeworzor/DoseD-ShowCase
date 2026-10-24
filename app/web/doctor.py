"""Doctor dashboard routes (/d/...)."""
import json
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse

from .. import config, service, similar, store
from . import render

router = APIRouter(prefix="/d")


def _go(url):
    return RedirectResponse(url, status_code=303)


def _counts(cards):
    counts = {s: 0 for s in service.STATUS_ORDER}
    for c in cards:
        counts[c["status"]] += 1
    return counts


@router.get("")
def dashboard(request: Request, status: str = ""):
    cards = service.queue()
    shown = [c for c in cards if c["status"] == status] if status else cards
    at_target = sum(1 for c in cards if c["pillars"][0] == c["pillars"][1])
    return render(request, "doctor/dashboard.html", cards=shown, all_cards=cards, counts=_counts(cards),
                  actions=service.pending_actions(cards), status=status, at_target=at_target, nav="queue")


@router.get("/patient/{pid}")
def patient_card(request: Request, pid: str, msg: str = "", err: str = "", open: str = ""):
    """open = which decision panel to show expanded again after an error."""
    c = service.card(pid)
    order = next((o for o in store.state()["orders"] if o["patient"] == pid), None)
    cards = service.queue()
    ids = [x["p"]["id"] for x in cards]
    i = ids.index(pid) if pid in ids else 0
    pos = {"i": i + 1, "n": len(ids), "prev": ids[i - 1] if i > 0 else None, "next": ids[i + 1] if i + 1 < len(ids) else None}
    return render(request, "doctor/patient.html", c=c, msg=msg, err=err, open=open, order=order, pos=pos,
                  counts=_counts(cards), nav="queue")


@router.post("/patient/{pid}/decide")
def decide(pid: str, action: str = Form(...), reason: str = Form(""), note: str = Form(""),
           choice: str = Form(""), evidence_ok: str = Form("")):
    """choice = 'drug|dose' from the dose picker. Both outcomes return to the decision box (#decide)."""
    drug, _, dose = choice.partition("|")
    try:
        text = service.decide(pid, action, config.settings()["app"]["doctor_name"], reason=reason, note=note,
                              drug=drug or None, dose=float(dose) if dose else None, evidence_ok=bool(evidence_ok))
        return _go(f"/d/patient/{pid}?msg={quote(text)}#decide")
    except service.DecisionError as e:
        return _go(f"/d/patient/{pid}?err={quote(str(e))}&open={quote(action)}#decide")


@router.get("/trust")
def trust(request: Request):
    path = config.DATA / "eval" / "results.json"
    results = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    return render(request, "doctor/trust.html", results=results, counts=_counts(service.queue()), nav="trust")


@router.get("/audit")
def audit(request: Request):
    return render(request, "doctor/audit.html", audit=store.state()["audit"][:200],
                  counts=_counts(service.queue()), nav="audit")


@router.post("/reset")
def reset():
    store.reset()
    similar.episodes.cache_clear()
    return _go("/d")


@router.post("/reload")
def reload_config():
    """Apply edits to config/*.yaml without restarting the server."""
    config.reload()
    similar.episodes.cache_clear()
    return _go("/d")


@router.get("/upload/{pid}/{name}")
def upload(pid: str, name: str):
    """A photo or video a patient sent (check-in attachment or BP photo)."""
    path = service.upload_path(pid, name)
    if not path:
        raise HTTPException(404)
    return FileResponse(path)
