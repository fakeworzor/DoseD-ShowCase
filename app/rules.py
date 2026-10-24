"""Titration rules engine. Reads config/rules.yaml and config/drugs.yaml; no clinical numbers live here.
The AI never decides a dose: this module does, deterministically, and the doctor signs."""
from . import config

LABELS = {
    "sbp_avg": "systolic BP", "dia_avg": "diastolic BP", "hr_avg": "heart rate", "k": "K+", "cr": "creatinine",
    "cr_rise_pct": "creatinine rise %", "egfr": "eGFR", "weight_gain_3d": "weight gain (3 days)",
    "k_rising_n": "K+ rising results", "nyha": "NYHA class", "dizzy": "dizziness", "nsaid": "NSAID use",
    "lab_age_days": "lab age (days)", "reading_days": "days with readings",
}
OPS = {"<": lambda a, b: a < b, "<=": lambda a, b: a <= b, ">": lambda a, b: a > b,
       ">=": lambda a, b: a >= b, "==": lambda a, b: a == b, "!=": lambda a, b: a != b}


def _label(metric: str) -> str:
    return config.tr(LABELS.get(metric, metric))


def check(conditions: list, m: dict) -> tuple[bool, list[str]]:
    """All conditions must hold. Returns (ok, reasons for any that failed)."""
    failed = []
    for metric, op, value in conditions:
        actual = m.get(metric)
        if actual is None:
            failed.append(config.tr("no {label} data").format(label=_label(metric)))
        elif not OPS[op](actual, value):
            sym = {">=": "≥", "<=": "≤", "==": "=", "!=": "≠"}.get(op, op)
            failed.append(config.tr("needs {label} {sym} {value} (now {actual})").format(
                label=_label(metric), sym=sym, value=f"{value:g}", actual=f"{actual:g}"))
    return not failed, failed


def describe(conditions: list, m: dict) -> list[dict]:
    """Each condition as a readable line with pass/fail, for the doctor's 'Safety checks' panel."""
    out = []
    for metric, op, value in conditions:
        actual = m.get(metric)
        ok = actual is not None and OPS[op](actual, value)
        now = config.tr("no data") if actual is None else config.tr("now {v}").format(v=f"{actual:g}")
        sym = {">=": "≥", "<=": "≤", "==": "=", "!=": "≠"}.get(op, op)
        out.append({"text": f"{_label(metric)} {sym} {value:g} ({now})", "ok": ok, "metric": metric,
                    "short": f"{_label(metric)} {sym} {value:g}"})
    return out


def checks_for(candidate: dict, m: dict) -> list[dict]:
    """Step conditions plus every hard stop that applies to this drug class."""
    r = config.rules()
    rows = []
    if candidate["kind"] == "step":
        rows += describe(r["step_rules"][candidate["cls"]]["require"], m)
    else:
        rule = next(ch for ch in r["changes"] if ch["id"] == candidate["rule_id"])
        rows += describe(rule["when"], m)
    for row in rows:
        row["kind"] = "need"
    for s in r["stops"]:
        if candidate["cls"] in s["classes"]:
            triggered = check(s["when"], m)[0]
            text = config.tr(s["text"])
            rows.append({"text": config.tr("No stop: {text}").format(text=text[0].lower() + text[1:]), "ok": not triggered,
                         "kind": "stop", "short": text, "metrics": [c[0] for c in s["when"]]})
    return rows


def _med(patient: dict, drug_class: str):
    drugs = config.drugs()
    for med in patient["meds"]:
        if drugs[med["drug"]]["class"] == drug_class:
            return med
    return None


def next_dose(drug_key: str, dose: float):
    ladder = config.drugs()[drug_key]["ladder"]
    higher = [d for d in ladder if d > dose]
    return higher[0] if higher else None


def at_target(drug_key: str, dose: float) -> bool:
    target = config.drugs()[drug_key]["target"]
    return target is not None and dose >= target


def data_issues(patient: dict, m: dict) -> list[str]:
    req = config.rules()["data_requirements"]
    issues = []
    if m["reading_days"] < req["min_reading_days"]:
        issues.append(config.tr("Home readings on {n} of the last {window} days (needs {need})").format(
            n=m["reading_days"], window=req["reading_window_days"], need=req["min_reading_days"]))
    if m["lab_age_days"] is None:
        issues.append(config.tr("No K+ and creatinine result on record"))
    elif m["lab_age_days"] > req["lab_max_age_days"]:
        issues.append(config.tr("Latest K+ and creatinine are {days} days old (limit {limit})").format(
            days=m["lab_age_days"], limit=req["lab_max_age_days"]))
    for lab in patient.get("pending_labs", []):
        issues.append(config.tr("{test} ordered {date}, no result yet").format(test=config.tr(lab["test"]), date=lab["ordered"]))
    return issues


def propose(patient: dict, m: dict) -> dict:
    r = config.rules()
    drugs = config.drugs()
    result = {"status": None, "primary": None, "others": [], "stops": [], "warnings": [], "issues": [],
              "priority_rule": None, "friction": "low"}

    if m["red_flag"]:
        result["status"] = "red_flag"
        return result

    result["issues"] = data_issues(patient, m)
    if result["issues"]:
        result["status"] = "waiting"
        return result

    stops = [s for s in r["stops"] if check(s["when"], m)[0]]
    result["stops"] = stops
    blocked_classes = {c: s for s in stops for c in s["classes"]}

    prio = next(p for p in r["priority"] if check(p["when"], m)[0])
    result["priority_rule"] = prio
    order = prio["order"]

    candidates = []
    for med in patient["meds"]:
        d = drugs[med["drug"]]
        cls = d["class"]
        if cls not in r["step_rules"] or at_target(med["drug"], med["dose"]):
            continue
        to = next_dose(med["drug"], med["dose"])
        if to is None:
            continue
        rule = r["step_rules"][cls]
        ok, why = check(rule["require"], m)
        candidates.append({"kind": "step", "drug": med["drug"], "cls": cls, "from": med["dose"], "to": to,
                           "rule_id": rule["id"], "rule_text": rule["text"], "eligible": ok, "why_not": why,
                           "blocked_by": blocked_classes.get(cls), "rank": order.index(cls) if cls in order else 99})

    for ch in r["changes"]:
        target_cls = drugs[ch["drug"]]["class"]
        if ch.get("missing_class") and _med(patient, ch["missing_class"]):
            continue
        if ch.get("from_class"):
            current = _med(patient, ch["from_class"])
            if not current or (ch.get("from_at_target") and not at_target(current["drug"], current["dose"])):
                continue
        ok, why = check(ch["when"], m)
        cand = {"kind": ch["kind"], "drug": ch["drug"], "cls": target_cls, "from": None, "to": ch["dose"],
                "rule_id": ch["id"], "rule_text": ch["text"], "note": ch.get("note"), "eligible": ok, "why_not": why,
                "blocked_by": blocked_classes.get(target_cls), "rank": 100}
        if ch.get("from_class"):
            current = _med(patient, ch["from_class"])
            cand["replaces"] = current["drug"]
            cand["replaces_dose"] = current["dose"]
        candidates.append(cand)

    candidates.sort(key=lambda c: c["rank"])
    usable = [c for c in candidates if c["eligible"] and not c["blocked_by"]]
    held = [c for c in candidates if c["blocked_by"]]  # a hard stop is shown as a hold, eligible or not

    if usable:
        primary = usable[0]
        result["status"] = "review" if primary["kind"] != "step" else "ready"
    elif held:
        primary = held[0]
        result["status"] = "hold"
    else:
        primary = None
        result["status"] = "no_change"
    result["primary"] = primary

    for c in candidates:
        if c is primary:
            continue
        if c["blocked_by"]:
            c["not_chosen"] = config.tr("Blocked: {why}").format(why=config.tr(c["blocked_by"]["text"]))
        elif not c["eligible"]:
            c["not_chosen"] = config.tr("Not yet: {why}").format(why="; ".join(c["why_not"]))
        else:
            c["not_chosen"] = config.tr("Lower priority ({why})").format(why=config.tr(prio["text"]).lower())
        result["others"].append(c)

    result["warnings"] = [w for w in r["warnings"] if check(w["when"], m)[0]]
    if primary and (primary["kind"] != "step" or result["warnings"]):
        result["friction"] = "review"
    return result
