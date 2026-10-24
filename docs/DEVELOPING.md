# Developing Dose-D

Heart-failure dose titration between clinic visits. Patients send home blood pressure (photo or typed) and how they feel; rules suggest the next dose step or drug change; the doctor approves, edits or rejects. The AI reads Thai messages and monitor photos but never decides a dose.

All patient data here is **synthetic**. Rule thresholds are **placeholders** until a cardiologist signs them.

## Run it

```sh
./run.sh                      # first run creates .venv and installs packages
```

- Doctor dashboard: http://localhost:8000/d
- Patient app: http://localhost:8000/p (opens patient 0012). On a phone on the same Wi-Fi, use your Mac's IP instead of localhost.
- Optional: `cp .env.example .env`. `GEMINI_API_KEY` turns on BP photo reading (free key from Google AI Studio); `ANTHROPIC_API_KEY` turns on reading Thai messages. Without it, the keyword safety net runs alone and photo reading asks the patient to type instead.

Tests: `.venv/bin/python -m pytest -q` · Trust numbers: `.venv/bin/python scripts/eval.py`

## Where to change what

Most changes are one file. `./run.sh` restarts itself when a `.py` or `.yaml` file changes; after editing demo data in `data/seed/`, also click **Reset demo data**.

| To change | Edit |
|---|---|
| Thresholds, stop rules, step order, drug switches, recheck days | `config/rules.yaml` |
| Drugs, ladders, targets, tablet strengths, Thai/English names, name printed on the packet (`box_name`) | `config/drugs.yaml` |
| Red-flag, dizziness and NSAID words (the safety net) | `config/redflags.yaml` |
| Every Thai word on the patient screens and the patient message | `config/text_th.yaml` |
| English patient screens (same keys; a missing key shows the Thai) | `config/text_en.yaml` |
| Thai doctor screens (key = the English text; missing = shown in English) | `config/doctor_th.yaml` |
| Who reads BP photos (Gemini or Claude) and which model | `llm.photo_reader`, `llm.gemini_model` in `config/settings.yaml`; key in `.env` |
| Patient's steps for today (order, which ones appear, where each leads next) | `today_steps()` in `app/service.py`; wording is `step_*` in `config/text_th.yaml` |
| Hospital, nurse, doctor name, demo date, AI model, IRIS connection, similar-patient bands, unusual-reading limits (`plausible`), attachment size/count (`uploads`) | `config/settings.yaml` |
| Colours, fonts, spacing | `static/theme.css` (tokens only; patient look is the `body.patient` block) |
| Doctor layout / patient layout, incl. phone/tablet breakpoints at the end of each file | `static/doctor.css` / `static/patient.css` |
| Patient logo and home-tile pictures | `static/illus/*.svg` (swap a file, keep the name) |
| Demo patients, incl. past visits shown on the card's flowsheet (`visits:`) | `data/seed/patients.yaml`, then Reset demo data |
| Number of past visits on the flowsheet / drug-class colours | `doctor.flowsheet_visits` in `config/settings.yaml` / `--cls-*` in `static/theme.css` |
| Past cases for "similar patients" | `data/seed/episodes.yaml` (and `scripts/seed_iris.py` if using IRIS) |
| Guideline citations on the card | `data/seed/guidelines.yaml` |
| Test messages for the trust numbers | `data/eval/testset.yaml` |
| A doctor screen / a patient screen | `templates/doctor/*.html` / `templates/patient/*.html` |

## How the code fits together

| File | Job |
|---|---|
| `app/rules.py` | Deterministic rules: data checks, hard stops, next step, drug changes, warnings. No clinical numbers in code. |
| `app/clinical.py` | Averages, K+ trend, eGFR (CKD-EPI 2021), tablet wording in Thai. |
| `app/similar.py` | "What happened to similar patients": hard filters + note similarity. Can only add caution. |
| `app/iris_db.py` | InterSystems IRIS: one hybrid SQL query (filters + `VECTOR_COSINE`), FHIR MedicationRequest on approval. |
| `app/llm.py` | Claude: read Thai messages (with exact quotes), read BP photos, word the patient message. Keyword net always runs. |
| `app/checker.py` | Rejects AI text with any number or drug not in the order, and quotes not found in the patient's message. |
| `app/service.py` | Builds cards and the queue, applies decisions, patient check-ins. |
| `app/store.py` | State in `data/state.json`, built from `data/seed`. Swap for a database later. |
| `app/web/` | Routes: `patient.py` (/p), `doctor.py` (/d). |

## Safety design in one list

- The AI never chooses a drug or dose. Rules propose; the doctor signs every change.
- Hard stops and data checks run before any suggestion. Red flags escalate and skip the queue.
- The keyword safety net always runs; the AI can add caution, never remove a keyword hit.
- "Similar patients" can turn a step into a hold, never the reverse, and shows no numbers below `min_cases`.
- Approve stays locked until the doctor ticks "reviewed the evidence" when a warning, drug change or hold applies.
- Patient messages: drug, tablets and dates come from the order; AI wording must pass the checker or the template is sent.
- LINE carries a link only, never health data.

## InterSystems IRIS (for the InterSystems prize)

1. Start IRIS with the InterSystems FHIR/AI kit ([FHIR-AI-Hackathon-Kit](https://github.com/intersystems-community/FHIR-AI-Hackathon-Kit), `./docker-iris-fhir/start.sh`). Docker Desktop must be running.
2. `.venv/bin/python scripts/seed_iris.py` loads the past cases into `DoseD.Episode` with a `VECTOR` column.
3. Set `store.backend: iris` in `config/settings.yaml`. The card then says "InterSystems IRIS"; approvals also POST a FHIR `MedicationRequest`.

If IRIS is down, the app falls back to local search and says so on the card.

**Not yet tested against a live IRIS instance** (Docker was not running when this was built). Run step 2 first and fix any SQL error it prints.

## Known limits

- Demo date is fixed (`app.demo_today`) so synthetic dates line up.
- The in-page camera needs HTTPS (or localhost). Over plain http on a phone it falls back to the phone's own camera button.
- Patient attachments are stored in `data/uploads/` (not in git) and are not read by the AI.
- No login: doctor name comes from settings; patients are addressed by ID in the URL. Add hospital SSO and LINE Login before any real use.
- Embeddings are character n-grams (offline, Thai-friendly, simple). Swap `similar.embed()` for a real model later.

## Languages

Each app has a TH/EN switch (patient: top strip; doctor: top bar), remembered per device in a cookie. Patient app defaults to Thai, doctor app to English. Messages sent to the patient on LINE stay Thai.
