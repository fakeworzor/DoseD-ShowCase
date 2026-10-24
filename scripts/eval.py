"""Measure the trust numbers on data/eval/testset.yaml and save them for the Trust page.
Run:  .venv/bin/python scripts/eval.py            (uses Claude if ANTHROPIC_API_KEY is set)
      .venv/bin/python scripts/eval.py --keywords (keyword safety net only, for comparison)"""
import datetime as dt
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import checker, config, llm  # noqa: E402


def main():
    use_ai = "--keywords" not in sys.argv and llm.available()
    path = config.DATA / "eval" / "testset.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    msgs = data["messages"]

    rf_total = rf_hit = exact = false_rf = 0
    misses = []
    for m in msgs:
        a = llm.analyse_text(m["text"], allow_ai=use_ai)
        got = {"red_flag": a["red_flag"], "dizzy": a["dizzy"], "nsaid": a["nsaid"]}
        want = {k: m[k] for k in got}
        if want["red_flag"]:
            rf_total += 1
            rf_hit += got["red_flag"]
        elif got["red_flag"]:
            false_rf += 1
        if got == want:
            exact += 1
        else:
            wrong = [f"{k}: expected {want[k]}, got {got[k]}" for k in got if got[k] != want[k]]
            misses.append({"text": m["text"], "problem": "; ".join(wrong)})
        print(("OK  " if got == want else "MISS"), m["text"], got)

    ch = data["checker"]
    caught = sum(1 for t in ch["must_reject"] if not checker.check_text(t, ch["facts"])[0])
    passed = sum(1 for t in ch["must_accept"] if checker.check_text(t, ch["facts"])[0])

    n = len(msgs)
    pct = lambda a, b: f"{a / b * 100:.0f}%" if b else "–"  # noqa: E731
    results = {
        "run_at": dt.datetime.now().isoformat(timespec="minutes"),
        "testset": str(path.relative_to(config.ROOT)), "n": n, "synthetic": True,
        "reader": f"AI + keywords ({config.settings()['llm']['model']})" if use_ai else "keywords only",
        "metrics": [
            {"name": "Red-flag recall", "value": pct(rf_hit, rf_total), "detail": f"{rf_hit} of {rf_total} caught",
             "target": "100%", "pass": rf_hit == rf_total},
            {"name": "Messages read fully right", "value": pct(exact, n), "detail": f"{exact} of {n}",
             "target": "≥95%", "pass": exact / n >= 0.95},
            {"name": "False red flags", "value": str(false_rf), "detail": "over-escalations (safe but noisy)",
             "target": "low", "pass": false_rf <= 2},
            {"name": "Checker catches changed facts", "value": pct(caught, len(ch["must_reject"])),
             "detail": f"{caught} of {len(ch['must_reject'])} altered messages blocked, {passed} of {len(ch['must_accept'])} correct ones allowed",
             "target": "100%", "pass": caught == len(ch["must_reject"]) and passed == len(ch["must_accept"])},
        ],
        "misses": misses,
    }
    out = config.DATA / "eval" / "results.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nSaved {out}. Reader: {results['reader']}")
    for k in results["metrics"]:
        print(f"  {k['name']}: {k['value']} ({k['detail']})")


if __name__ == "__main__":
    main()
