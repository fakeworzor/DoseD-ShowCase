"""Shared template setup for the patient and doctor routes."""
import datetime as dt

from fastapi.templating import Jinja2Templates

from .. import clinical, config, llm, service

templates = Jinja2Templates(directory=str(config.ROOT / "templates"))
env = templates.env
env.globals.update(
    S=config.settings,            # S().app.hospital etc.
    RULES=config.rules,
    T=config.text,                # patient wording, current language
    asset=lambda path: f"/static/{path}?v={int((config.ROOT / 'static' / path).stat().st_mtime)}",
    _=config.tr,                  # doctor wording: {{ _("English text") }} -> Thai from config/doctor_th.yaml
    lang=lambda: config.LANG.get(),
    day_label=clinical.day_label,
    drug_name=lambda key: config.drugs()[key]["name_th" if config.LANG.get() == "th" else "name_en"],
    patient_name=lambda p: p["name_th"] if config.LANG.get() == "th" else p.get("name_en", p["name_th"]),
    drugs=config.drugs,
    dose_label=clinical.dose_label,
    tablets=clinical.tablets_th,
    box_label=clinical.box_label,  # name as printed on the packet
    freq_th=clinical.freq_th,
    thai_date=clinical.thai_date,
    today=config.today,
    ai_on=llm.available,
    photo_reader=llm.photo_reader,
    STATUS_LABEL=service.STATUS_LABEL,
    steps=service.today_steps,    # patient's steps for today
)


def fmt(value, digits=1):
    if value is None:
        return "–"
    return f"{value:.{digits}f}" if isinstance(value, float) and value != int(value) else f"{value:g}"


def signed(value):
    if value is None:
        return "–"
    return f"+{value:g}" if value > 0 else f"{value:g}"


env.filters["fmt"] = fmt
env.filters["d1"] = lambda v: "–" if v is None else f"{v:.1f}"  # 4 -> 4.0 (K+, weight)
env.filters["d2"] = lambda v: "–" if v is None else f"{v:.2f}"  # 1 -> 1.00 (creatinine)
env.filters["dose"] = lambda v: "–" if v is None else f"{v:g}"  # 1.25 stays 1.25 (fmt would round it)
env.filters["todate"] = lambda s: dt.date.fromisoformat(str(s)[:10])
env.filters["signed"] = signed


def render(request, name, **ctx):
    return templates.TemplateResponse(request, name, ctx)
