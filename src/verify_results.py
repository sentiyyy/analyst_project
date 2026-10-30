"""Compare MapReduce and DuckDB outputs row by row without loading large CSVs into RAM."""
from __future__ import annotations

import argparse
import csv
import itertools
import json
from pathlib import Path

from dotenv import load_dotenv

from src.s3io import upload_json

load_dotenv()

FIELDS = {
    "skills.csv": ["skill", "count"],
    "pairs.csv": ["skill_1", "skill_2", "count"],
    "triples.csv": ["skill_1", "skill_2", "skill_3", "count"],
}

def rows(path: Path):
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            yield tuple(row[field] for field in reader.fieldnames[:-1]) + (int(row["count"]),)

def compare_files(left: Path, right: Path) -> dict:
    compared = 0
    for compared, pair in enumerate(itertools.zip_longest(rows(left), rows(right), fillvalue=None), start=1):
        a, b = pair
        if a != b:
            return {"match": False, "first_difference_row": compared, "mapreduce_row": a, "duckdb_row": b}
    return {"match": True, "rows_compared": compared}

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", default="1000,5000,10000,all")
    parser.add_argument("--output-dir", default="results")
    parser.add_argument("--bucket", default="hh-lake")
    parser.add_argument("--no-upload", action="store_true")
    args = parser.parse_args()
    base = Path(args.output_dir)
    report = {"variant": "10. Аналитик", "verified": True, "comparisons": {}}
    for size in [x.strip() for x in args.sizes.split(",") if x.strip()]:
        report["comparisons"][size] = {}
        for filename in FIELDS:
            left = base / "mapreduce" / size / filename
            right = base / "duckdb" / size / filename
            if not left.exists() or not right.exists():
                result = {"match": False, "error": f"Не найден файл: {left if not left.exists() else right}"}
            else:
                result = compare_files(left, right)
            report["comparisons"][size][filename] = result
            report["verified"] = report["verified"] and bool(result.get("match"))
    report_path = base / "verification.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if not args.no_upload:
        upload_json(report, args.bucket, "results/analyst/verification.json")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["verified"]:
        raise SystemExit(2)

if __name__ == "__main__":
    main()