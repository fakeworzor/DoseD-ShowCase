"""Clinical calculations from a patient record: averages, trends, eGFR, tablet wording."""
import datetime as dt
from statistics import mean

from . import config


def parse_dt(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value)


def egfr_ckd_epi_2021(cr_mg_dl: float, age: int, sex: str) -> float:
    """CKD-EPI 2021 creatinine equation (race-free). Creatinine in mg/dL."""
    female = sex.upper() == "F"
    kappa, alpha = (0.7, -0.241) if female else (0.9, -0.302)
    ratio = cr_mg_dl / kappa
    value = 142 * min(ratio, 1) ** alpha * max(ratio, 1) ** -1.200 * 0.9938 ** age
    return round(value * (1.012 if female else 1), 1)


def rising_run(values: list) -> int:
    """How many results in a row (ending at the latest) are each higher than the one before."""
    run = 1
    for i in range(len(values) - 1, 0, -1):
        if values[i] > values[i - 1]:
            run += 1
        else:
            break
    return run if len(values) > 1 else 0


def metrics(patient: dict, symptoms: dict | None = None) -> dict:
    """Everything the rules can test, computed from the record. Missing values are None."""
    rules = config.rules()
    today = config.today()
    window = rules["data_requirements"]["reading_window_days"]
    start = today - dt.timedelta(days=window - 1)

    recent = [r for r in patient["readings"] if parse_dt(r["at"]).date() >= start]
    labs = sorted(patient["labs"], key=lambda x: x["date"])
    latest = labs[-1] if labs else None
    baseline = labs[0] if labs else None

    weights = sorted(((parse_dt(r["at"]), r["weight"]) for r in patient["readings"] if r.get("weight")), key=lambda x: x[0])
    weight_gain = None
    if weights:
        cutoff = dt.datetime.combine(today - dt.timedelta(days=3), dt.time.max)
        earlier = [w for t, w in weights if t <= cutoff]
        if earlier:
            weight_gain = round(weights[-1][1] - earlier[-1], 1)

    m = {
        "sbp_avg": round(mean(r["sys"] for r in recent)) if recent else None,
        "dia_avg": round(mean(r["dia"] for r in recent)) if recent else None,
        "hr_avg": round(mean(r["pulse"] for r in recent)) if recent else None,
        "reading_count": len(recent),
        "reading_days": len({parse_dt(r["at"]).date() for r in recent}),
        "photo_count": sum(1 for r in recent if r.get("method") == "photo"),
        "k": latest["k"] if latest else None,
        "cr": latest["cr"] if latest else None,
        "lab_date": latest["date"] if latest else None,
        "lab_age_days": (today - dt.date.fromisoformat(latest["date"])).days if latest else None,
        "cr_rise_pct": round((latest["cr"] - baseline["cr"]) / baseline["cr"] * 100, 1) if latest and baseline else None,
        "egfr": egfr_ckd_epi_2021(latest["cr"], patient["age"], patient["sex"]) if latest else None,
        "k_rising_n": rising_run([lab["k"] for lab in labs]),
        "weight_gain_3d": weight_gain,
        "weight": weights[-1][1] if weights else None,
        "nyha": patient.get("nyha"),
        "dizzy": 0,
        "nsaid": 0,
        "red_flag": False,
    }
    if symptoms:
        m["dizzy"] = 1 if symptoms.get("dizzy") else 0
        m["nsaid"] = 1 if symptoms.get("nsaid") else 0
        m["red_flag"] = bool(symptoms.get("red_flag"))
    return m


def window_average(patient: dict, field: str, end: dt.date, days: int):
    start = end - dt.timedelta(days=days - 1)
    vals = [r[field] for r in patient["readings"] if r.get(field) and start <= parse_dt(r["at"]).date() <= end]
    return round(mean(vals)) if vals else None


# ---------- wording for patients ----------

def tablets_th(drug_key: str, dose: float, t: dict | None = None) -> str:
    """'ครึ่งเม็ด', '1 เม็ด', '1 เม็ดครึ่ง' (or English) from the dispensed tablet strength.
    t = wording to use; defaults to the current language. Messages sent to the patient pass Thai."""
    t = t or config.text()
    drug = config.drugs()[drug_key]
    strength = drug.get("tablet_mg")
    if strength == "per_dose" or not strength:
        label = drug.get("labels", {}).get(dose, f"{dose:g} {t.get('unit_mg', 'มก.')}")
        return f"{t['tab_whole'].format(n=1)} ({label})"
    count = dose / strength
    whole, frac = int(count), round(count - int(count), 2)
    if frac == 0:
        return t["tab_whole"].format(n=whole)
    if frac == 0.5:
        return t["tab_half"] if whole == 0 else t["tab_whole_half"].format(n=whole)
    return f"{dose:g} {t.get('unit_mg', 'มก.')} ({t['tab_quarter_note']})"


def box_label(drug_key: str, dose: float) -> str:
    """Name and strength as printed on the dispensed packet, e.g. 'Bisoprolol 5 mg'. Set box_name in drugs.yaml."""
    drug = config.drugs()[drug_key]
    name = drug.get("box_name") or drug["name_en"]
    if drug.get("tablet_mg") == "per_dose":
        return f"{name} {drug.get('labels', {}).get(dose, f'{dose:g} mg')}"
    return f"{name} {drug['tablet_mg']:g} mg" if drug.get("tablet_mg") else name


def freq_th(drug_key: str, t: dict | None = None) -> str:
    t = t or config.text()
    return t["freq_bid"] if config.drugs()[drug_key]["freq"] == "bid" else t["freq_daily"]


def dose_label(drug_key: str, dose) -> str:
    """Doctor-facing dose text, e.g. '12.5 mg once daily' or '24/26 mg twice daily'."""
    if dose is None:
        return "none"
    drug = config.drugs()[drug_key]
    amount = drug.get("labels", {}).get(dose, f"{dose:g} mg")
    return f"{amount} {config.tr('twice daily' if drug['freq'] == 'bid' else 'once daily')}"


def day_label(d: dt.date) -> str:
    """Short date in the current language: '17 ต.ค.' or '17 Oct'."""
    return thai_date(d) if config.LANG.get() == "th" else f"{d.day} {d.strftime('%b')}"


def thai_date(d: dt.date) -> str:
    months = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]
    return f"{d.day} {months[d.month - 1]}"
