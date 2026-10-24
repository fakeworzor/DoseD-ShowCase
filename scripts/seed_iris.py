"""Load the past titration episodes into InterSystems IRIS (table DoseD.Episode, with a VECTOR column).
1. Start IRIS (InterSystems FHIR/AI kit: ./docker-iris-fhir/start.sh)
2. .venv/bin/python scripts/seed_iris.py
3. In config/settings.yaml set store.backend: iris"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import iris_db, similar  # noqa: E402


def main():
    rows = similar.episodes()
    iris_db.create_table()
    iris_db.load_episodes(rows)
    print(f"Loaded {len(rows)} episodes into {iris_db.TABLE}.")
    sample = rows[0]
    hits = iris_db.hybrid_search(sample["drug"], sample["from"], sample["to"], sample["egfr"], sample["k_before"], sample["vec"])
    print(f"Test query returned {len(hits)} rows; top: {hits[0] if hits else None}")


if __name__ == "__main__":
    main()
