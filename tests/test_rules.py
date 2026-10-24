"""Run: .venv/bin/python -m pytest -q
These pin the demo behaviour. If you change rules.yaml on purpose, update the expected values here."""
import copy

import pytest

from app import checker, clinical, config, llm, rules, service, store


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    """Fresh in-memory copy of the seed data; never touches data/state.json."""
    monkeypatch.setitem(config.settings()["llm"], "mode", "off")
    monkeypatch.setattr(store, "save", lambda: None)
    store._state = store._seed()
    yield
    store._state = None


def status(pid):
    return service.card(pid)["status"]


def test_demo_statuses():
    assert {p: status(p) for p in ["0012", "0019", "0040", "0007", "0024", "0031"]} == {
        "0012": "ready", "0019": "hold", "0040": "review", "0007": "ready", "0024": "waiting", "0031": "red_flag"}


def test_low_bp_steps_mra_first():
    pr = service.card("0012")["prop"]["primary"]
    assert (pr["drug"], pr["from"], pr["to"]) == ("spironolactone", 12.5, 25)


def test_high_k_holds_mra():
    p = copy.deepcopy(store.patient("0012"))
    p["labs"].append({"date": "2026-10-17", "k": 5.4, "cr": 1.12})
    m = clinical.metrics(p)
    prop = rules.propose(p, m)
    assert prop["status"] == "hold" and prop["primary"]["drug"] == "spironolactone"
    assert any(s["id"] == "HIGH-K" for s in prop["stops"])


def test_nsaid_keyword_blocks_step():
    a = llm.analyse_text("กินไอบูโพรเฟนแก้ปวดเข่า", allow_ai=False)
    assert a["nsaid"]
    p = store.patient("0012")
    prop = rules.propose(p, clinical.metrics(p, a))
    assert prop["status"] == "hold"


def test_red_flag_keyword():
    assert llm.analyse_text("นอนราบไม่ได้ทั้งคืน", allow_ai=False)["red_flag"]


def test_guard_holds_on_matching_words():
    c = service.card("0019")
    assert c["guard"] and c["status"] == "hold"


def test_similar_numbers_for_demo():
    s = service.card("0012")["similar"]
    assert (s["n"], s["bad"], s["lost"], s["k_median"]) == (34, 3, 2, 0.3)


def test_egfr():
    assert clinical.egfr_ckd_epi_2021(1.12, 66, "M") == pytest.approx(72.5, abs=0.2)


def test_tablets_wording():
    assert clinical.tablets_th("spironolactone", 12.5) == "ครึ่งเม็ด"
    assert clinical.tablets_th("spironolactone", 25) == "1 เม็ด"
    assert clinical.tablets_th("bisoprolol", 7.5) == "1 เม็ดครึ่ง"


def test_checker_blocks_changed_numbers_and_drugs():
    facts = {"numbers": ["1", "18"], "drugs": ["spironolactone"]}
    assert checker.check_text("สไปโรโนแลกโตน 1 เม็ด เริ่ม 18 ต.ค.", facts)[0]
    assert not checker.check_text("สไปโรโนแลกโตน 2 เม็ด", facts)[0]
    assert not checker.check_text("สไปโรโนแลกโตน 1 เม็ด และอีนาลาพริล", facts)[0]


def test_approve_requires_evidence_when_k_rising():
    with pytest.raises(service.DecisionError):
        service.decide("0012", "approve", "Dr Test")
    service.decide("0012", "approve", "Dr Test", evidence_ok=True)
    assert next(m for m in store.patient("0012")["meds"] if m["drug"] == "spironolactone")["dose"] == 25


def test_hold_needs_reason_to_override():
    with pytest.raises(service.DecisionError):
        service.decide("0019", "approve", "Dr Test", evidence_ok=True)


def test_unusual_reading_warns_but_impossible_blocks():
    p = store.patient("0012")
    assert service.check_reading(p, 104, 66, 61) is None
    assert service.check_reading(p, 160, 92, 70)["level"] == "warn"   # far from the 7-day average: can be sent
    assert service.check_reading(p, 260, 90, 70)["level"] == "warn"   # outside the usual range: can be sent
    assert service.check_reading(p, 80, 90, 70)["level"] == "block"   # top below bottom: must be fixed


def test_flowsheet_rebuilds_past_doses():
    past = service.card("0012")["flow"]["past"]
    bisoprolol = [next(m["dose"] for m in col["meds"] if m["drug"] == "bisoprolol") for col in past]
    assert [col["date"] for col in past] == ["2026-08-25", "2026-09-08", "2026-09-22", "2026-10-06"]
    assert bisoprolol == [5, 5, 7.5, 10]


def test_doctor_can_choose_any_dose_with_guards():
    doc = "Dr. Test"
    with pytest.raises(service.DecisionError):  # same as now
        service.decide("0012", "change", doc, drug="spironolactone", dose=12.5, evidence_ok=True)
    with pytest.raises(service.DecisionError):  # held drug, going up, no reason
        service.decide("0019", "change", doc, drug="enalapril", dose=10, evidence_ok=True)
    service.decide("0012", "change", doc, drug="bisoprolol", dose=7.5, evidence_ok=True)  # a step down needs no reason
    assert next(m["dose"] for m in store.patient("0012")["meds"] if m["drug"] == "bisoprolol") == 7.5


def test_other_reason_needs_a_note():
    with pytest.raises(service.DecisionError):
        service.decide("0007", "reject", "Dr. Test", reason="Other")
    service.decide("0007", "reject", "Dr. Test", reason="Other", note="Away this week")
