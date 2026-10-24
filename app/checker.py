"""Claim checker: AI-written text may only repeat facts it was given.
Any number or drug name in the text that is not in the facts -> the text is rejected and a template is used."""
import re

from . import config

NUMBER = re.compile(r"\d+(?:[.,/]\d+)?")


def _numbers(text: str) -> set[str]:
    return {n.replace(",", ".") for n in NUMBER.findall(text)}


def _drug_names() -> dict[str, str]:
    names = {}
    for key, d in config.drugs().items():
        if key == "classes":
            continue
        names[d["name_th"]] = key
        names[d["name_en"].lower()] = key
    return names


def check_text(text: str, facts: dict) -> tuple[bool, list[str]]:
    """facts = {'numbers': iterable of allowed numbers (as text), 'drugs': iterable of allowed drug keys}."""
    problems = []
    allowed_numbers = {str(n).replace(",", ".") for n in facts.get("numbers", [])}
    for n in _numbers(text) - allowed_numbers:
        problems.append(f"number not in the order: {n}")
    allowed_drugs = set(facts.get("drugs", []))
    lowered = text.lower()
    for name, key in _drug_names().items():
        if name.lower() in lowered and key not in allowed_drugs:
            problems.append(f"drug not in the order: {name}")
    return not problems, problems


def quote_found(quote: str, source: str) -> bool:
    """An AI 'quote' must really appear in the patient's message."""
    def norm(s):
        return re.sub(r"\s+", "", s or "")
    return bool(quote) and norm(quote) in norm(source)
