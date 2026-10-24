"""FastAPI app. Run: ./run.sh  (or .venv/bin/uvicorn app.main:app --reload)"""
import logging

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .web import doctor, patient

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

app = FastAPI(title="Dose-D", docs_url="/api/docs")
app.mount("/static", StaticFiles(directory=config.ROOT / "static"), name="static")
app.include_router(patient.router)
app.include_router(doctor.router)


@app.get("/")
def home():
    return RedirectResponse("/d")


class Language:
    """Sets config.LANG for each request from a cookie: lang_d for /d (default English), lang_p for the patient
    app (default Thai). Plain ASGI middleware so the value reaches the route and the templates."""

    def __init__(self, asgi):
        self.asgi = asgi

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            doctor = scope["path"].startswith("/d")
            cookies = dict(part.strip().split("=", 1) for header, value in scope["headers"] if header == b"cookie"
                           for part in value.decode("latin-1").split(";") if "=" in part)
            lang = cookies.get("lang_d" if doctor else "lang_p", "en" if doctor else "th")
            config.LANG.set(lang if lang in config.LANGS else "th")
        await self.asgi(scope, receive, send)


app.add_middleware(Language)


@app.get("/lang/{who}/{code}")
def set_language(who: str, code: str, to: str = "/"):
    """The TH/EN switch. who = p (patient app) or d (doctor app); remembered on this device for a year."""
    resp = RedirectResponse(to if to.startswith("/") and not to.startswith("//") else "/", status_code=303)
    if who in ("p", "d") and code in config.LANGS:
        resp.set_cookie(f"lang_{who}", code, max_age=365 * 24 * 3600, samesite="lax")
    return resp
