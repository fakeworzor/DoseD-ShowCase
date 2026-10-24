"""Patient app routes (/p/...). Opened from a LINE link; Thai; one task per screen."""
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse

from .. import config, llm, rules, service, store
from . import render

router = APIRouter(prefix="/p")


def _go(url):
    return RedirectResponse(url, status_code=303)


def _p(pid):
    return store.patient(pid)


async def _read_media(request: Request) -> tuple[list[dict], str]:
    """Check-in attachments within settings.uploads limits. Returns (files, error text).
    Read from the raw form because an empty picker sends a blank part that typed File() params reject."""
    cfg, t = config.settings()["uploads"], config.text()
    limit = int(cfg["max_mb"] * 1024 * 1024)
    files = [f for f in (await request.form()).getlist("media") if getattr(f, "filename", "")]
    if len(files) > cfg["max_files"]:
        return [], t["media_too_many"].format(n=cfg["max_files"])
    out = []
    for f in files:
        kind = (f.content_type or "").split("/")[0]
        if kind not in ("image", "video"):
            return [], t["media_bad_type"]
        data = await f.read(limit + 1)
        if len(data) > limit:
            return [], t["media_too_big"].format(mb=cfg["max_mb"])
        out.append({"data": data, "type": f.content_type})
    return out, ""


@router.get("")
def default_patient():
    return _go(f"/p/{config.settings()['app']['demo_patient']}")


@router.get("/{pid}")
def home(request: Request, pid: str, complete: int = 0):
    steps = service.today_steps(pid)
    all_done = all(s["done"] for s in steps)
    return render(request, "patient/home.html", p=_p(pid), steps=steps, all_done=all_done,
                  popup=bool(complete) and all_done)


@router.get("/{pid}/bp")
def bp(request: Request, pid: str):
    return render(request, "patient/bp.html", p=_p(pid), sent=service.today_sent(pid))


@router.get("/{pid}/bp/photo")
def photo_form(request: Request, pid: str, error: str = ""):
    return render(request, "patient/photo.html", p=_p(pid), error=error)


@router.post("/{pid}/bp/photo")
async def photo_submit(request: Request, pid: str, image: UploadFile = File(...)):
    limit = int(config.settings()["uploads"]["max_mb"] * 1024 * 1024)
    data = await image.read(limit + 1)
    if len(data) > limit:
        return render(request, "patient/photo.html", p=_p(pid),
                      error=config.text()["media_too_big"].format(mb=config.settings()["uploads"]["max_mb"]))
    if not data:
        return _go(f"/p/{pid}/bp/photo")
    photo = service.save_media(pid, [{"data": data, "type": image.content_type or "image/jpeg"}], prefix="bp-")[0]["file"]
    reading = llm.read_bp_photo(data, image.content_type or "image/jpeg")
    if not reading or not reading["readable"] or not reading["confident"] or None in (
            reading["systolic"], reading["diastolic"], reading["pulse"]):
        # Could not read all 3 numbers (or the AI is off): the patient can still send the photo for the doctor.
        store.state()["drafts"][pid] = {"photo": photo, "unread": True, "note": (reading or {}).get("note", "")}
        store.save()
        return _go(f"/p/{pid}/bp/photo/unread")
    store.state()["drafts"][pid] = {"sys": reading["systolic"], "dia": reading["diastolic"], "pulse": reading["pulse"],
                                    "method": "photo", "ihb": reading.get("irregular_icon", False), "photo": photo}
    store.save()
    return _go(f"/p/{pid}/bp/confirm")


@router.get("/{pid}/bp/photo/unread")
def photo_unread(request: Request, pid: str):
    draft = store.state()["drafts"].get(pid)
    if not draft or not draft.get("unread"):
        return _go(f"/p/{pid}/bp/photo")
    return render(request, "patient/photo_unread.html", p=_p(pid), d=draft)


@router.post("/{pid}/bp/photo/send")
def photo_send(pid: str):
    draft = store.state()["drafts"].pop(pid, None)
    if draft and draft.get("unread"):
        service.add_photo_only(pid, draft["photo"], draft.get("note", ""))
    store.save()
    return _go(service.next_step_url(pid))


@router.get("/{pid}/upload/{name}")
def upload(pid: str, name: str):
    """The patient's own BP photo, shown back to them on the 'numbers not read' screen."""
    path = service.upload_path(pid, name)
    if not path:
        raise HTTPException(404)
    return FileResponse(path)


@router.post("/{pid}/reset")
def reset(pid: str):
    """Demo only: put this patient back to the seed data. Reached from the ☰ menu."""
    store.reset_patient(pid)
    return _go(f"/p/{pid}")


@router.get("/{pid}/bp/today")
def bp_today(request: Request, pid: str, saved: int = 0):
    """What the patient sent today, each reading with an 'edit' button."""
    nxt = next((s for s in service.today_steps(pid) if not s["done"]), None)
    return render(request, "patient/bp_today.html", p=_p(pid), sent=service.today_sent(pid), saved=saved,
                  next_href=nxt["href"] if nxt else f"/p/{pid}")


@router.get("/{pid}/bp/type")
def type_form(request: Request, pid: str, edit: str = ""):
    """edit = id of a reading sent today: the form opens with its numbers to correct."""
    draft = store.state()["drafts"].get(pid)
    edit = edit or (draft or {}).get("edit", "")  # back from the confirm screen while correcting
    if edit and not draft:
        r = next((x for x in service.today_sent(pid)["readings"] if x["id"] == edit), None)
        if not r:
            return _go(f"/p/{pid}/bp/today")
        draft = {"sys": r["sys"], "dia": r["dia"], "pulse": r["pulse"]}
    return render(request, "patient/type.html", p=_p(pid), draft=draft, edit=edit)


@router.post("/{pid}/bp/type")
def type_submit(pid: str, sys: int = Form(...), dia: int = Form(...), pulse: int = Form(...), edit: str = Form("")):
    store.state()["drafts"][pid] = {"sys": sys, "dia": dia, "pulse": pulse, "method": "typed", "edit": edit}
    store.save()
    return _go(f"/p/{pid}/bp/confirm")


@router.get("/{pid}/bp/confirm")
def confirm(request: Request, pid: str):
    draft = store.state()["drafts"].get(pid)
    if not draft:
        return _go(f"/p/{pid}/bp")
    return render(request, "patient/confirm.html", p=_p(pid), d=draft, problem=None)


@router.post("/{pid}/bp/confirm")
def confirm_submit(request: Request, pid: str, force: int = Form(0), ihb: str = Form("")):
    """An unusual reading ('warn') can be sent after the patient confirms it; 'block' must be fixed."""
    p, draft = _p(pid), store.state()["drafts"].get(pid)
    if not draft:
        return _go(f"/p/{pid}/bp")
    draft["ihb"] = bool(ihb)
    problem = service.check_reading(p, draft["sys"], draft["dia"], draft["pulse"])
    if problem and (problem["level"] == "block" or not force):
        store.save()
        return render(request, "patient/confirm.html", p=p, d=draft, problem=problem)
    store.state()["drafts"].pop(pid)
    if draft.get("edit"):
        service.edit_reading(pid, draft["edit"], draft["sys"], draft["dia"], draft["pulse"],
                             flag=problem["code"] if problem else None, ihb=draft["ihb"])
        return _go(f"/p/{pid}/bp/today?saved=1")
    service.add_reading(pid, draft["sys"], draft["dia"], draft["pulse"], draft["method"],
                        flag=problem["code"] if problem else None, ihb=draft["ihb"])
    return _go(service.next_step_url(pid))


@router.get("/{pid}/checkin")
def checkin(request: Request, pid: str):
    sent = service.today_sent(pid)["checkin"]  # already answered today: open with those answers to edit
    prev = dict((sent["analysis"].get("answers") or {}), note="" if sent["text"] == "(no message)" else sent["text"]) if sent else {}
    return render(request, "patient/checkin.html", p=_p(pid), error="", prev=prev, sent=sent)


@router.post("/{pid}/checkin")
async def checkin_submit(request: Request, pid: str, breathless: str = Form("no"), orthopnea: str = Form("no"),
                         dizzy: str = Form("no"), swelling: str = Form("no"), note: str = Form("")):
    answers = {"breathless": breathless, "orthopnea": orthopnea, "dizzy": dizzy, "swelling": swelling}
    files, error = await _read_media(request)
    if error:
        return render(request, "patient/checkin.html", p=_p(pid), error=error, prev=dict(answers, note=note),
                      sent=service.today_sent(pid)["checkin"])
    analysis = service.add_checkin(pid, answers, note, files)
    if analysis["red_flag"]:
        return render(request, "patient/emergency.html", p=_p(pid))
    return _go(service.next_step_url(pid))


@router.get("/{pid}/meds")
def meds(request: Request, pid: str):
    p = _p(pid)
    pending = [o for o in service.patient_orders(pid) if not o["started"]]
    changed = {o["drug"] for o in pending}
    unchanged = [m for m in p["meds"] if m["drug"] not in changed]
    return render(request, "patient/meds.html", p=p, pending=pending, unchanged=unchanged,
                  at_target=rules.at_target)


@router.post("/{pid}/meds/started")
def started(pid: str):
    service.confirm_started(pid)
    return _go(service.next_step_url(pid))
