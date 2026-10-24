"""Builds what the screens show and applies the doctor's decisions. Glue between store, rules, similar, llm."""
import datetime as dt
import logging
import mimetypes
import re

from . import clinical, config, llm, rules, similar, store

log = logging.getLogger("dose-d.service")

STATUS_ORDER = ["red_flag", "hold", "review", "ready", "waiting", "no_change", "signed"]
STATUS_LABEL = {"red_flag": "Red flag", "hold": "Hold", "review": "Needs review", "ready": "Ready",
                "waiting": "Waiting for data", "no_change": "No change", "signed": "Signed today"}
PILLARS = [("BB",), ("RASI", "ARNI"), ("MRA",), ("SGLT2I",)]


def _recent_message(p: dict):
    window = config.rules()["data_requirements"]["reading_window_days"]
    start = config.today() - dt.timedelta(days=window - 1)
    msgs = [m for m in p.get("messages", []) if clinical.parse_dt(m["at"]).date() >= start]
    return sorted(msgs, key=lambda m: m["at"])[-1] if msgs else None


def _drug(key: str) -> dict:
    return config.drugs()[key]


def _last_change(p: dict) -> dt.date | None:
    dates = [h["date"] for h in p.get("med_history", [])] or [p["plan"]["agreed"]]
    return dt.date.fromisoformat(max(dates))


def _signed_today(p: dict) -> dict | None:
    today = config.today().isoformat()
    for d in store.state()["decisions"]:
        if d["patient"] == p["id"] and d["at"].startswith(today) and d["action"] in ("approve", "change", "reject", "confirm_hold"):
            return d
    return None


def pillars_at_target(p: dict) -> tuple[int, int]:
    done = 0
    for classes in PILLARS:
        med = next((m for m in p["meds"] if _drug(m["drug"])["class"] in classes), None)
        if med and rules.at_target(med["drug"], med["dose"]):
            done += 1
    return done, len(PILLARS)


def order_fields(p: dict, cand: dict, to_dose=None) -> dict:
    """Facts for the patient message, all taken from the order."""
    t = config.text_th()
    to = to_dose if to_dose is not None else cand["to"]
    start = config.today() + dt.timedelta(days=1)
    lab = config.today() + dt.timedelta(days=config.rules()["recheck_days"].get(cand["cls"], 7))
    old = clinical.tablets_th(cand["drug"], cand["from"], t) if cand.get("from") else "ไม่มี"
    if cand["kind"] == "switch":
        old = f"{_drug(cand['replaces'])['name_th']} {clinical.tablets_th(cand['replaces'], cand['replaces_dose'], t)}"
    return {"drug": _drug(cand["drug"])["name_th"], "old": old, "new": clinical.tablets_th(cand["drug"], to, t),
            "freq": clinical.freq_th(cand["drug"], t), "start": clinical.thai_date(start), "lab": clinical.thai_date(lab),
            "_start": start, "_lab": lab, "_to": to}


def message_for(p: dict, cand: dict, to_dose=None, use_ai: bool = False) -> dict:
    t = config.text_th()
    f = order_fields(p, cand, to_dose)
    public = {k: v for k, v in f.items() if not k.startswith("_")}
    template = t["order_message"].format(**public)
    if not use_ai:
        return {"text": template, "source": "template", "fields": f}
    numbers = set()
    for v in public.values():
        numbers |= set(re.findall(r"\d+(?:[.,/]\d+)?", str(v)))
    drugs = {cand["drug"]} | ({cand["replaces"]} if cand.get("replaces") else set())
    text, source = llm.word_message({"fields": public, "numbers": numbers, "drugs": drugs}, template)
    return {"text": text, "source": source, "fields": f}


def card(pid: str) -> dict:
    p = store.patient(pid)
    msg = _recent_message(p)
    analysis = msg.get("analysis") if msg else None
    m = clinical.metrics(p, analysis)
    prop = rules.propose(p, m)
    c = {"p": p, "m": m, "prop": prop, "msg": msg, "analysis": analysis, "status": prop["status"],
         "similar": None, "guard": None, "guideline": None, "preview": None, "checks": [], "signed": _signed_today(p)}

    primary = prop["primary"]
    if primary and primary["kind"] == "step" and m["egfr"] is not None and m["k"] is not None:
        c["similar"] = similar.search(primary["drug"], primary["from"], primary["to"], m["egfr"], m["k"],
                                      msg["text"] if msg else None)
        c["guard"] = similar.guard(c["similar"])
        if c["guard"] and c["status"] in ("ready", "review"):
            c["status"] = "hold"
            prop["friction"] = "review"
    if primary:
        c["checks"] = rules.checks_for(primary, m)
        c["guideline"] = config.guidelines().get(primary["rule_id"])
        c["preview"] = message_for(p, primary)
    if c["signed"]:
        c["status"] = "signed"

    last = _last_change(p)
    due = last + dt.timedelta(days=config.rules()["review_interval_days"])
    c["overdue_days"] = (config.today() - due).days
    c["last_change"] = last
    c["pillars"] = pillars_at_target(p)
    labs = sorted(p["labs"], key=lambda x: x["date"])
    c["k_prev"] = labs[-2]["k"] if len(labs) > 1 else None
    c["cr_prev"] = labs[-2]["cr"] if len(labs) > 1 else None
    c["sbp_prev"] = clinical.window_average(p, "sys", last, 3)
    c["hr_prev"] = clinical.window_average(p, "pulse", last, 3)
    c["label"] = STATUS_LABEL[c["status"]]
    c["flow"] = flowsheet(c)
    c["choices"] = dose_choices(c)
    c["buttons"] = buttons(c)
    return c


CLASS_ORDER = ["BB", "RASI", "ARNI", "MRA", "SGLT2I"]
# metric -> flowsheet row, so a triggered stop or warning tints the matching Today cell
ROW_OF = {"sbp_avg": "bp", "dia_avg": "bp", "hr_avg": "hr", "k": "k", "k_rising_n": "k", "cr": "cr", "cr_rise_pct": "cr",
          "egfr": "cr", "weight_gain_3d": "weight", "dizzy": "symptoms", "nsaid": "symptoms", "nyha": "symptoms"}


def _row_meds(doses: dict, changes: dict) -> list[dict]:
    """One column's GDMT list in pillar order. Diuretics are left out (shown in the header)."""
    rows = []
    for drug, dose in doses.items():
        d = _drug(drug)
        if d["class"] not in CLASS_ORDER:
            continue
        ch = changes.get(drug)
        rows.append({"drug": drug, "cls": d["class"], "name": d["name_en"], "dose": dose,
                     "from": ch.get("from") if ch else None, "changed": bool(ch),
                     "dir": None if not ch else "new" if ch.get("from") is None else "up" if dose > ch["from"] else "down",
                     "target": rules.at_target(drug, dose)})
    return sorted(rows, key=lambda r: CLASS_ORDER.index(r["cls"]))


def flowsheet(c: dict) -> dict:
    """Visit-by-visit table for the patient card: past reviews left to right, Today on the right.
    Doses at each review are rebuilt by walking med_history back from today's meds, so no dose is stored twice."""
    p, m, today = c["p"], c["m"], config.today().isoformat()
    labs = {lab["date"]: lab for lab in p["labs"]}
    hist = sorted(p.get("med_history", []), key=lambda h: h["date"], reverse=True)
    cols = {v["date"]: dict(v) for v in p.get("visits", [])}
    for h in hist:
        cols.setdefault(h["date"], {"date": h["date"], "kind": h["by"]})
    last_visit = max((d for d in cols if d < today), default="")
    for d in labs:  # a lab after the last review is today's lab; earlier ones get a column if no review that day
        if d <= last_visit:
            cols.setdefault(d, {"date": d, "kind": "lab"})
    past = sorted((v for d, v in cols.items() if d < today), key=lambda v: v["date"])
    past = past[-config.settings().get("doctor", {}).get("flowsheet_visits", 4):]

    doses = {med["drug"]: med["dose"] for med in p["meds"]}
    todays = [h for h in hist if h["date"] >= today]  # a change signed today shows in the Today column
    for h in todays:
        _undo(doses, h)
    for col in reversed(past):
        changes = {h["drug"]: h for h in hist if h["date"] == col["date"]}
        col.update(meds=_row_meds(doses, changes), lab=labs.get(col["date"]),
                   day=dt.date.fromisoformat(col["date"]).strftime("%-d %b"))
        for h in changes.values():
            _undo(doses, h)

    prev_vitals = next((v for v in reversed(past) if v.get("sys")), {})
    prev_lab = next((v["lab"] for v in reversed(past) if v.get("lab")), None)
    a = c["analysis"] or {}
    flagged = [k for k in ("breathless", "orthopnea", "chest_pain", "fainting", "dizziness", "swelling")
               if (a.get("answers") or {}).get(k) == "yes" or ((a.get("ai") or {}).get(k) or {}).get("answer") == "yes"]
    flagged += [{"red_flag": "red-flag words", "dizzy": "dizziness", "nsaid": "NSAID use"}.get(k, k)
                for k, words in (a.get("keywords") or {}).items() if words and k not in flagged]
    warn = {ROW_OF[cond[0]] for s in c["prop"]["stops"] + c["prop"]["warnings"] for cond in s["when"] if cond[0] in ROW_OF}
    if c["status"] == "red_flag" or flagged:
        warn.add("symptoms")
    if any(r.get("flag") or r.get("ihb") for r in _window_readings(p)) or any(
            x["at"][:10] >= _window_start().isoformat() for x in p.get("photos", [])):
        warn.add("bp")
    # one colour per Today cell: stop (red) beats warn (amber) beats ok (green, a passed check on this row)
    stop = {ROW_OF[x] for s in c["prop"]["stops"] for cond in s["when"] for x in [cond[0]] if x in ROW_OF}
    stop |= {ROW_OF[ch["metric"]] for ch in c["checks"] if not ch["ok"] and ch.get("metric") in ROW_OF}
    if c["status"] == "red_flag":
        stop.add("symptoms")
    ok = {ROW_OF[ch["metric"]] for ch in c["checks"] if ch["ok"] and ch.get("metric") in ROW_OF}
    if c["checks"] and not flagged and c["msg"]:
        ok.add("symptoms")
    state = {row: "stop" if row in stop else "warn" if row in warn else "ok" if row in ok else ""
             for row in ("symptoms", "weight", "bp", "hr", "cr", "k")}
    return {
        "past": past,
        "today": {
            "nyha": _roman(p.get("nyha")),
            "symptoms": ", ".join(dict.fromkeys(config.tr(k.replace("_", " ")) for k in flagged)) if flagged
                        else config.tr("None" if c["msg"] else "No check-in"),
            "symptoms_src": config.tr(c["msg"]["from"] + " check-in") if c["msg"] else "",
            "weight": m.get("weight"), "weight_delta": _delta(m.get("weight"), prev_vitals.get("weight")),
            "sys": m["sbp_avg"], "dia": m["dia_avg"], "sys_delta": _delta(m["sbp_avg"], prev_vitals.get("sys")),
            "hr": m["hr_avg"], "hr_delta": _delta(m["hr_avg"], prev_vitals.get("hr")),
            "k": m["k"], "k_delta": _delta(m["k"], prev_lab and prev_lab["k"]),
            "cr": m["cr"], "cr_delta": _delta(m["cr"], prev_lab and prev_lab["cr"]),
            "lab_age": m["lab_age_days"], "readings": m["reading_count"],
            "flagged": [r for r in _window_readings(p) if r.get("flag")],
            "edited": [r for r in _window_readings(p) if r.get("edited")],
            "ihb": sum(1 for r in _window_readings(p) if r.get("ihb")),
            "attachments": len((c["msg"] or {}).get("attachments") or []),
            "photos_unread": [x for x in p.get("photos", []) if x["at"][:10] >= _window_start().isoformat()],
            "meds": _row_meds({med["drug"]: med["dose"] for med in p["meds"]}, {h["drug"]: h for h in todays}),
            "warn": warn, "state": state,
        },
        "others": {o["drug"]: o["not_chosen"] for o in c["prop"]["others"]},
        "extra": [med for med in p["meds"] if _drug(med["drug"])["class"] not in CLASS_ORDER],
    }


def _window_start() -> dt.date:
    return config.today() - dt.timedelta(days=config.rules()["data_requirements"]["reading_window_days"] - 1)


def _window_readings(p: dict) -> list[dict]:
    """Readings inside the window the averages use (rules.data_requirements.reading_window_days)."""
    return [r for r in p["readings"] if r["at"][:10] >= _window_start().isoformat()]


def _undo(doses: dict, h: dict):
    """Step one dose change backwards."""
    if h.get("from") is None:
        doses.pop(h["drug"], None)
    else:
        doses[h["drug"]] = h["from"]
    if h.get("replaces"):
        doses[h["replaces"]] = h.get("replaces_dose")


def _delta(now, before):
    if now is None or before is None:
        return None
    return round(now - before, 2)


def _roman(n):
    return {1: "I", 2: "II", 3: "III", 4: "IV"}.get(n, n)


def queue() -> list[dict]:
    cards = [card(p["id"]) for p in store.patients()]
    return sorted(cards, key=lambda c: (STATUS_ORDER.index(c["status"]), -c["overdue_days"]))


def pending_actions(cards: list[dict]) -> list[dict]:
    items = []
    for c in cards:
        p = c["p"]
        if c["status"] == "red_flag" and not any(e.get("acknowledged") for e in p.get("escalations", [])):
            items.append({"kind": "red", "title": config.tr("Red flag") + f" · HN {p['id']}",
                          "sub": config.tr("Escalated to the on-call team. Acknowledge."),
                          "since": c["msg"]["at"][11:16] if c["msg"] else "", "pid": p["id"]})
        for lab in p.get("pending_labs", []):
            items.append({"kind": "amber", "title": config.tr("Lab pending") + f" · HN {p['id']}",
                          "sub": config.tr("{test}, ordered {date}").format(test=config.tr(lab["test"]), date=lab["ordered"]),
                          "since": lab["ordered"][5:], "pid": p["id"]})
    for o in store.state()["orders"]:
        if not o.get("started"):
            items.append({"kind": "blue", "title": config.tr("Not started yet") + f" · HN {o['patient']}",
                          "sub": config.tr("{drug} change sent {at}").format(drug=o["drug_en"], at=o["at"][5:16]), "since": o["at"][11:16], "pid": o["patient"]})
    return items


# ---------- decisions ----------

class DecisionError(ValueError):
    pass


def dose_choices(c: dict) -> list[dict]:
    """Every titrated drug the patient is on, with its allowed doses, for the doctor's 'Choose a dose' picker."""
    pr, stops = c["prop"]["primary"], c["prop"]["stops"]
    rows = []
    for med in sorted(c["p"]["meds"], key=lambda m: CLASS_ORDER.index(_drug(m["drug"])["class"])
                      if _drug(m["drug"])["class"] in CLASS_ORDER else 99):
        d = _drug(med["drug"])
        if d["class"] not in CLASS_ORDER or not d["ladder"]:
            continue
        rows.append({"drug": med["drug"], "cls": d["class"], "name": d["name_en"], "now": med["dose"], "target": d["target"],
                     "ladder": d["ladder"], "blocked": next((s["text"] for s in stops if d["class"] in s["classes"]), None),
                     "suggested": pr["to"] if pr and pr["kind"] == "step" and pr["drug"] == med["drug"] else None})
    return rows


# Statuses where "Reject" means nothing more than "no change today", so it takes one tap and logs this reason.
REJECT_ONE_TAP = {"hold": "Kept on hold (stop rule)", "waiting": "Waiting for data", "no_change": "No change suggested",
                  "red_flag": "Red flag: no dose change"}


def buttons(c: dict) -> dict | None:
    """Every patient gets the same three buttons: approve · edit · reject. What changes is how much each asks
    before it acts: 'tap' (one tap), 'confirm' (opens a short step: reason, evidence tick or dose) or 'off'."""
    if c["status"] == "signed":
        return None
    pr, st, review = c["prop"]["primary"], c["status"], c["prop"]["friction"] == "review"
    if not pr or st in ("red_flag", "waiting"):
        approve = {"mode": "off", "hint": {"red_flag": "No dose change on a red flag", "waiting": "Waiting for data"}.get(st, "Nothing to approve")}
    elif st == "hold":
        approve = {"mode": "confirm", "hint": "Override: reason needed"}
    elif review:
        approve = {"mode": "confirm", "hint": "Tick the evidence first"}
    else:
        approve = {"mode": "tap", "hint": "One tap"}
    edit = {"mode": "confirm", "hint": "Pick a dose"} if c["choices"] else {"mode": "off", "hint": "No titrated drugs"}
    reject = {"mode": "tap", "hint": "One tap · no change"} if st in REJECT_ONE_TAP else {"mode": "confirm", "hint": "Reason needed"}
    return {"approve": approve, "edit": edit, "reject": reject}


def decide(pid: str, action: str, doctor: str, reason: str = "", note: str = "", drug: str | None = None,
           dose: float | None = None, evidence_ok: bool = False) -> str:
    """approve = the suggestion as shown; change = a dose the doctor picks for any titrated drug;
    reject = no change today; confirm_hold; acknowledge (red flag). Raises DecisionError with a plain message."""
    c = card(pid)
    p, primary = c["p"], c["prop"]["primary"]
    now = config.now().isoformat(sep=" ")
    if reason == "Other" and not note.strip():
        raise DecisionError(config.tr("You chose 'Other'. Add a short note."))
    why = f"{reason}: {note.strip()}" if reason and note.strip() else reason or note.strip()
    needs_evidence = c["prop"]["friction"] == "review"
    if action == "approve":
        if not primary:
            raise DecisionError(config.tr("There is no suggestion to approve."))
        if c["status"] == "hold" and not reason:
            raise DecisionError(config.tr("This step is on hold. Pick a reason to override it."))
        if needs_evidence and not evidence_ok:
            raise DecisionError(config.tr("Tick 'I have reviewed the evidence' first."))
        _apply(p, primary, None, doctor, now, why)
    elif action == "change":
        row = next((r for r in dose_choices(c) if r["drug"] == drug), None)
        if not row or dose is None or dose not in row["ladder"]:
            raise DecisionError(config.tr("Pick a drug and a dose."))
        if dose == row["now"]:
            raise DecisionError(config.tr("That is the current dose. Pick a different one, or choose 'Not today'."))
        if row["blocked"] and dose > row["now"] and not reason:
            raise DecisionError(config.tr("A stop rule applies ({why}). Pick a reason to go up anyway.").format(why=config.tr(row["blocked"])))
        if c["status"] == "hold" and primary and drug == primary["drug"] and dose > row["now"] and not reason:
            raise DecisionError(config.tr("This drug's step is on hold. Pick a reason to go up anyway."))
        if needs_evidence and not evidence_ok:
            raise DecisionError(config.tr("Tick 'I have reviewed the evidence' first."))
        cand = {"kind": "step", "drug": drug, "cls": row["cls"], "from": row["now"], "to": dose,
                "rule_id": primary["rule_id"] if primary and primary["drug"] == drug else "DOCTOR-CHOICE",
                "rule_text": "Dose chosen by the doctor"}
        _apply(p, cand, dose, doctor, now, why)
    elif action == "reject":
        if not why and c["status"] in REJECT_ONE_TAP:
            why = REJECT_ONE_TAP[c["status"]]
        if not why:
            raise DecisionError(config.tr("Pick a reason for not changing today."))
    elif action == "acknowledge":
        for e in p.setdefault("escalations", []):
            e["acknowledged"] = now
        if not p["escalations"]:
            p["escalations"].append({"at": now, "acknowledged": now})
    elif action != "confirm_hold":
        raise DecisionError(f"Unknown action {action}")
    store.state()["decisions"].insert(0, {"at": now, "patient": pid, "action": action, "by": doctor, "reason": why,
                                          "drug": drug, "dose": dose,
                                          "rule_id": primary["rule_id"] if primary else None,
                                          "rules_version": config.rules()["version"]})
    store.audit(doctor, action, f"{why or ''} rule {primary['rule_id'] if primary else '-'}".strip(), pid)
    if action == "change":
        return config.tr("{drug} changed to {dose} mg and sent to the patient.").format(drug=_drug(drug)["name_en"], dose=f"{dose:g}")
    return config.tr({"approve": "Approved and sent to the patient.", "reject": "No change today. Logged.",
                      "acknowledge": "Red flag acknowledged.", "confirm_hold": "Hold confirmed. No change today."}[action])


def _apply(p: dict, cand: dict, to_dose, doctor: str, now: str, reason: str):
    msg = message_for(p, cand, to_dose, use_ai=True)
    f = msg["fields"]
    to = f["_to"]
    if cand["kind"] == "switch":
        p["meds"] = [m for m in p["meds"] if m["drug"] != cand["replaces"]]
    if cand["kind"] in ("add", "switch"):
        p["meds"].append({"drug": cand["drug"], "dose": to})
    else:
        next(m for m in p["meds"] if m["drug"] == cand["drug"])["dose"] = to
    p["med_history"].insert(0, {"date": config.today().isoformat(), "drug": cand["drug"], "from": cand.get("from"),
                                "to": to, "by": "remote", "replaces": cand.get("replaces"),
                                "replaces_dose": cand.get("replaces_dose")})
    p.setdefault("pending_labs", []).append({"test": "K+ and creatinine", "ordered": f["_lab"].isoformat()})
    order = {"id": store.new_id("ord"), "at": now, "patient": p["id"], "drug": cand["drug"],
             "drug_en": _drug(cand["drug"])["name_en"], "kind": cand["kind"], "from": cand.get("from"), "to": to,
             "replaces": cand.get("replaces"), "dose_text": clinical.dose_label(cand["drug"], to), "rule_id": cand["rule_id"],
             "by": doctor, "reason": reason, "message": msg["text"], "message_source": msg["source"],
             "start": f["_start"].isoformat(), "lab": f["_lab"].isoformat(), "started": None}
    store.state()["orders"].insert(0, order)
    _notify(p, order)
    if config.settings()["store"]["backend"] == "iris" and config.settings()["iris"]["write_fhir"]:
        try:
            from . import iris_db
            store.audit("system", "fhir", iris_db.write_medication_request(order), p["id"])
        except Exception as e:  # noqa: BLE001
            store.audit("system", "fhir_failed", f"{type(e).__name__}: {e}", p["id"])
    store.save()


def _notify(p: dict, order: dict):
    from . import line
    status = line.push(p.get("line_user_id"), config.text_th()["notify_text"])
    store.audit("system", "notify", status, p["id"])


# ---------- patient side ----------

def check_reading(p: dict, sys: int, dia: int, pulse: int) -> dict | None:
    """None if the reading looks fine. level 'block': cannot be right (top not above bottom), so fix the numbers.
    level 'warn': unusual; the patient may measure again or send it anyway, and the doctor sees it marked."""
    t, limits = config.text(), config.settings()["plausible"]
    if sys <= dia:
        return {"level": "block", "code": "order", "text": t["reading_block_order"]}
    for name, value in (("sys", sys), ("dia", dia), ("pulse", pulse)):
        lo, hi = limits[name]
        if not lo <= value <= hi:
            return {"level": "warn", "code": "range", "text": t["reading_unusual"].format(why=t["unusual_range"])}
    recent = clinical.window_average(p, "sys", config.today(), 7)
    if recent and abs(sys - recent) > limits["max_jump_sys"]:
        return {"level": "warn", "code": "jump", "text": t["reading_unusual"].format(why=t["unusual_jump"])}
    return None


def add_reading(pid: str, sys: int, dia: int, pulse: int, method: str, weight: float | None = None,
                flag: str | None = None, ihb: bool = False):
    """flag = check_reading code when the patient sent an unusual value anyway; ihb = monitor showed the
    irregular-heartbeat symbol."""
    p = store.patient(pid)
    p["readings"].append({"id": store.new_id("r"), "at": config.now().isoformat(sep=" ")[:16], "sys": sys, "dia": dia,
                          "pulse": pulse, "weight": weight, "method": method, "live": True, "flag": flag, "ihb": ihb})
    extra = "".join([f", sent despite warning ({flag})" if flag else "", ", irregular-heartbeat symbol" if ihb else ""])
    store.audit("patient", "reading", f"{sys}/{dia} pulse {pulse} ({method}{extra})", pid)
    store.save()


def today_sent(pid: str) -> dict:
    """What the patient sent today, for the 'review / edit' screens: readings (newest first), photos, check-in."""
    p, day = store.patient(pid), config.today().isoformat()
    readings = [r for r in p["readings"] if r.get("live") and r["at"].startswith(day)]
    for r in readings:  # readings saved before ids existed
        r.setdefault("id", store.new_id("r"))
    photos = [x for x in p.get("photos", []) if x["at"].startswith(day)]
    checkin = next((m for m in reversed(p.get("messages", [])) if m.get("live") and m["at"].startswith(day)), None)
    return {"readings": readings[::-1], "photos": photos[::-1], "checkin": checkin}


def edit_reading(pid: str, rid: str, sys: int, dia: int, pulse: int, flag: str | None = None, ihb: bool = False):
    """The patient corrects a reading they sent today. The original numbers are kept for the doctor."""
    r = next((x for x in today_sent(pid)["readings"] if x["id"] == rid), None)
    if not r:
        raise KeyError(rid)
    r.setdefault("original", f"{r['sys']}/{r['dia']} pulse {r['pulse']}")
    r.update(sys=sys, dia=dia, pulse=pulse, flag=flag, ihb=ihb, edited=config.now().isoformat(sep=" ")[:16])
    store.audit("patient", "reading_edit", f"{r['original']} -> {sys}/{dia} pulse {pulse}", pid)
    store.save()


def upload_path(pid: str, name: str):
    """Path of a stored upload, or None. Names are checked so no path can escape the uploads folder."""
    if not (re.fullmatch(r"[\w-]+", pid) and re.fullmatch(r"[\w.-]+", name)):
        return None
    path = config.ROOT / config.settings()["uploads"]["dir"] / pid / name
    return path if path.is_file() else None


def add_photo_only(pid: str, file: str, note: str = ""):
    """A BP photo whose numbers could not be read, sent so the doctor can read it. Not in any average."""
    p = store.patient(pid)
    p.setdefault("photos", []).append({"at": config.now().isoformat(sep=" ")[:16], "file": file, "note": note, "live": True})
    store.audit("patient", "reading", f"BP photo sent, numbers not read ({file})", pid)


def save_media(pid: str, files: list[dict], prefix: str = "") -> list[dict]:
    """Write uploads to uploads/<pid>/. files: [{data, type}]. Returns what the record stores."""
    folder = config.ROOT / config.settings()["uploads"]["dir"] / pid
    folder.mkdir(parents=True, exist_ok=True)
    stamp = config.now().strftime("%Y%m%d-%H%M%S")
    saved = []
    for i, f in enumerate(files, 1):
        ext = mimetypes.guess_extension(f["type"]) or ""
        name = f"{prefix}{stamp}-{i}{ext}"
        (folder / name).write_bytes(f["data"])
        saved.append({"file": name, "kind": f["type"].split("/")[0], "size": len(f["data"])})
    return saved


def add_checkin(pid: str, answers: dict, note: str, media: list[dict] | None = None) -> dict:
    """Yes/no answers are direct data; the free-text note is read by the AI plus the keyword net.
    Attached photos/videos are stored for the doctor and nurse; the AI does not read them."""
    p = store.patient(pid)
    analysis = llm.analyse_text(note) if note.strip() else llm.analyse_text("", allow_ai=False)
    analysis["answers"] = answers
    if answers.get("orthopnea") == "yes":
        analysis["red_flag"] = True
    if answers.get("dizzy") == "yes":
        analysis["dizzy"] = True
    at = config.now().isoformat(sep=" ")[:16]
    new_files = save_media(pid, media) if media else []
    earlier = today_sent(pid)["checkin"]
    if earlier:  # editing today's answers: keep what was sent before for the doctor and the audit log
        if earlier["analysis"].get("red_flag") or any(x["analysis"].get("red_flag") for x in earlier.get("previous", [])):
            analysis["red_flag"] = True  # an edit never clears a red flag; the doctor sees it until they act
            analysis["red_flag_earlier"] = True
        earlier.setdefault("previous", []).append({k: earlier[k] for k in ("at", "text", "analysis")})
        earlier.update(at=at, text=note.strip() or "(no message)", analysis=analysis, edited=at,
                       attachments=(earlier.get("attachments") or []) + new_files)
        store.audit("patient", "checkin_edit", "Patient changed today's check-in answers", pid)
    else:
        p.setdefault("messages", []).append({"at": at, "from": "patient", "text": note.strip() or "(no message)",
                                             "analysis": analysis, "live": True, "attachments": new_files})
    if analysis["red_flag"] and not any(e.get("acknowledged") is None for e in p.get("escalations", [])):
        p.setdefault("escalations", []).append({"at": at, "acknowledged": None})
        store.audit("system", "escalate", "Red flag from patient check-in", pid)
    store.save()
    return analysis


def patient_orders(pid: str) -> list[dict]:
    return [o for o in store.state()["orders"] if o["patient"] == pid]


def confirm_started(pid: str):
    for o in patient_orders(pid):
        if not o["started"]:
            o["started"] = config.now().isoformat(sep=" ")
    store.audit("patient", "started", "Confirmed new dose started", pid)
    store.save()


def today_steps(pid: str) -> list[dict]:
    """The patient's tasks for today, in order. Home lists them; finishing one leads to the next.
    Wording is in config/text_th.yaml (step_*); the medicine step appears only when the doctor changed a dose."""
    p, t, day = store.patient(pid), config.text(), config.today().isoformat()

    def done_today(items):
        return any(x.get("live") and x["at"].startswith(day) for x in items)

    steps = [
        {"key": "bp", "href": f"/p/{pid}/bp", "art": "bp", "done": done_today(p["readings"] + p.get("photos", []))},
        {"key": "checkin", "href": f"/p/{pid}/checkin", "art": "checkin", "done": done_today(p.get("messages", []))},
    ]
    orders = patient_orders(pid)
    if any(not o["started"] or o["started"].startswith(day) for o in orders):
        steps.append({"key": "meds", "href": f"/p/{pid}/meds", "art": "meds", "done": all(o["started"] for o in orders)})
    review = {"bp": f"/p/{pid}/bp/today", "checkin": f"/p/{pid}/checkin", "meds": f"/p/{pid}/meds"}
    current = next((s for s in steps if not s["done"]), None)
    for i, s in enumerate(steps, 1):
        s["review"] = review[s["key"]]
        s.update(n=i, title=t[f"step_{s['key']}"], sub=t[f"step_{s['key']}_sub"],
                 state="done" if s["done"] else "now" if s is current else "next")
    return steps


def next_step_url(pid: str) -> str:
    """Where to send the patient after finishing a step: the next unfinished one, or home with the 'all done' pop-up."""
    nxt = next((s for s in today_steps(pid) if not s["done"]), None)
    return nxt["href"] if nxt else f"/p/{pid}?complete=1"
