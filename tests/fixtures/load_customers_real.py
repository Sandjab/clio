import csv
import json
import argparse
from pathlib import Path


STATE_FILE = Path(__file__).resolve().parent.parent / "state.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True)
    args = parser.parse_args()

    customers = []
    with open(args.file) as f:
        for row in csv.DictReader(f):
            customers.append({"name": row["name"], "revenue": float(row["revenue"])})

    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    state["customers"] = customers
    STATE_FILE.write_text(json.dumps(state, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
