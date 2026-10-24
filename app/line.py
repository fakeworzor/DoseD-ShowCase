"""LINE push: a link only, never health data. Needs LINE_CHANNEL_TOKEN in .env and a patient line_user_id."""
from . import config


def push(user_id: str | None, text: str) -> str:
    token = config.env("LINE_CHANNEL_TOKEN")
    if not token or not user_id:
        return "LINE not configured: notification skipped (demo)"
    import httpx
    link = config.env("PUBLIC_BASE_URL", "") + "/p"
    r = httpx.post("https://api.line.me/v2/bot/message/push", timeout=10,
                   headers={"Authorization": f"Bearer {token}"},
                   json={"to": user_id, "messages": [{"type": "text", "text": f"{text}\n{link}"}]})
    return f"LINE push {r.status_code}"
