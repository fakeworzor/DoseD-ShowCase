"""InterSystems IRIS: past titration episodes with a VECTOR column, searched with one hybrid SQL query
(hard filters + VECTOR_COSINE), and FHIR MedicationRequest writes on approval.
Connection values: config/settings.yaml (iris) + .env (IRIS_USER, IRIS_PASSWORD)."""
import contextlib

from . import config

TABLE = "DoseD.Episode"


@contextlib.contextmanager
def cursor():
    import iris  # package: intersystems-irispython
    s = config.settings()["iris"]
    conn = iris.connect(hostname=s["host"], port=s["port"], namespace=s["namespace"],
                        username=config.env("IRIS_USER", "SuperUser"), password=config.env("IRIS_PASSWORD", "SYS"))
    cur = conn.cursor()
    try:
        yield cur
        conn.commit()
    finally:
        cur.close()  # Community Edition has a small connection limit: always close
        conn.close()


def create_table():
    dims = config.settings()["embedding"]["dims"]
    with cursor() as cur:
        with contextlib.suppress(Exception):
            cur.execute(f"DROP TABLE {TABLE}")
        cur.execute(f"""CREATE TABLE {TABLE} (
            EpisodeId VARCHAR(20), Drug VARCHAR(40), FromDose DOUBLE, ToDose DOUBLE, EGFR DOUBLE,
            KBefore DOUBLE, KAfter DOUBLE, Outcome VARCHAR(20), NoteTh VARCHAR(2000),
            NoteVec VECTOR(DOUBLE, {dims}))""")


def load_episodes(rows: list[dict]):
    sql = (f"INSERT INTO {TABLE} (EpisodeId, Drug, FromDose, ToDose, EGFR, KBefore, KAfter, Outcome, NoteTh, NoteVec) "
           "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, TO_VECTOR(?))")
    with cursor() as cur:
        for r in rows:
            cur.execute(sql, [r["id"], r["drug"], r["from"], r["to"], r["egfr"], r["k_before"], r["k_after"],
                              r["outcome"], r["note"], str(r["vec"])])


def hybrid_search(drug, from_dose, to_dose, egfr, k, qvec) -> list[dict]:
    """The InterSystems part: structured filters and vector similarity in one SQL query."""
    s = config.settings()["similar"]
    sql = f"""SELECT EpisodeId, EGFR, KBefore, KAfter, Outcome, NoteTh,
                     VECTOR_COSINE(NoteVec, TO_VECTOR(?, DOUBLE)) AS Sim
              FROM {TABLE}
              WHERE Drug = ? AND FromDose = ? AND ToDose = ?
                AND EGFR BETWEEN ? AND ? AND KBefore BETWEEN ? AND ?
              ORDER BY Sim DESC"""
    params = [str(qvec), drug, from_dose, to_dose, egfr - s["egfr_band"], egfr + s["egfr_band"],
              round(k - s["k_band"], 2), round(k + s["k_band"], 2)]
    with cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [{"id": r[0], "egfr": r[1], "k_before": r[2], "k_after": r[3], "outcome": r[4], "note": r[5],
             "sim": round(float(r[6]), 3)} for r in rows]


def write_medication_request(order: dict) -> str:
    """POST a FHIR R4 MedicationRequest for a signed order. Returns a short status for the audit log."""
    import httpx
    s = config.settings()["iris"]
    body = {
        "resourceType": "MedicationRequest", "status": "active", "intent": "order",
        "subject": {"identifier": {"system": "urn:dose-d:hn", "value": order["patient"]}},
        "medicationCodeableConcept": {"text": order["drug_en"]},
        "authoredOn": order["at"].replace(" ", "T"),
        "dosageInstruction": [{"text": order["dose_text"]}],
        "note": [{"text": f"Dose-D rule {order['rule_id']}; signed by {order['by']}"}],
    }
    r = httpx.post(f"{s['fhir_base']}/MedicationRequest", json=body, timeout=15,
                   auth=(config.env("IRIS_USER", "SuperUser"), config.env("IRIS_PASSWORD", "SYS")),
                   headers={"Content-Type": "application/fhir+json", "Accept": "application/fhir+json"})
    r.raise_for_status()
    return f"FHIR MedicationRequest created ({r.headers.get('Location', 'no location header')})"
