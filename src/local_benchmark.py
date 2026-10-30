"""Reproduce the local baseline/optimized benchmark on a filtered analyst CSV."""
from __future__ import annotations

import argparse
import csv
import itertools
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

from src.common import ANALYST_ROLES, parse_skills

load_dotenv()

COLUMNS = ["vacancy_id", "snapshot_date", "published_at", "area", "name", "role",
           "salary_from", "salary_to", "currency", "gross", "experience", "schedule",
           "employment", "key_skills"]

def prepare_filtered(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with source.open("r", encoding="utf-8-sig", newline="") as src, target.open("w", encoding="utf-8-sig", newline="") as dst:
        reader = csv.DictReader(src)
        writer = csv.DictWriter(dst, fieldnames=COLUMNS)
        writer.writeheader()
        for row in reader:
            if (row.get("role") or "").strip() in ANALYST_ROLES:
                writer.writerow({key: (row.get(key, "") or "").strip() for key in COLUMNS})

def worker(path: Path, mode: str) -> dict:
    try:
        import resource as resource_module
    except ImportError:
        resource_module = None
    started = time.perf_counter()
    skills_total, pairs_total, triples_total = Counter(), Counter(), Counter()
    rows = 0
    use_cache = mode == "optimized"
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            rows += 1
            skills = parse_skills(row.get("key_skills", ""), use_cache=use_cache)
            skills_total.update(skills)
            pairs_total.update(itertools.combinations(skills, 2))
            triples_total.update(itertools.combinations(skills, 3))
    result = {
        "mode": mode,
        "rows": rows,
        "unique_skills": len(skills_total),
        "unique_pairs": len(pairs_total),
        "unique_triples": len(triples_total),
        "skill_occurrences": sum(skills_total.values()),
        "pair_occurrences": sum(pairs_total.values()),
        "triple_occurrences": sum(triples_total.values()),
        "elapsed_seconds": round(time.perf_counter() - started, 4),
    }
    if resource_module is not None:
        result["peak_rss_mb"] = round(resource_module.getrusage(resource_module.RUSAGE_SELF).ru_maxrss / 1024, 2)
    else:
        result["peak_rss_mb"] = None  # Use profile_runs.py for cross-platform RSS sampling.
    print(json.dumps(result, ensure_ascii=False))
    return result

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-csv", default="data/it_vacancies_2024.csv")
    parser.add_argument("--filtered-csv", default="data/analyst_vacancies_local.csv")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--worker", choices=["baseline", "optimized"])
    parser.add_argument("--csv", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        worker(Path(args.csv or args.filtered_csv), args.worker)
        return
    if args.trials < 3:
        raise SystemExit("По требованиям задания нужно не менее трёх попыток.")
    source = Path(args.source_csv)
    filtered = Path(args.filtered_csv)
    if not filtered.exists():
        if not source.exists():
            raise SystemExit(f"Не найден источник: {source}")
        prepare_filtered(source, filtered)
    records = []
    expected_counts = None
    for mode in ["baseline", "optimized"]:
        for trial in range(1, args.trials + 1):
            command = [sys.executable, "-m", "src.local_benchmark", "--worker", mode, "--csv", str(filtered)]
            completed = subprocess.run(command, capture_output=True, text=True, check=True)
            line = next(line for line in reversed(completed.stdout.splitlines()) if line.strip().startswith("{"))
            result = json.loads(line)
            result["trial"] = trial
            result["mode"] = mode
            records.append(result)
            counts = {k: result[k] for k in ["rows", "unique_skills", "unique_pairs", "unique_triples", "skill_occurrences", "pair_occurrences", "triple_occurrences"]}
            if expected_counts is None:
                expected_counts = counts
            elif counts != expected_counts:
                raise RuntimeError("Контрольные числа baseline и optimized различаются.")
    output = Path("results/local_preliminary")
    output.mkdir(parents=True, exist_ok=True)
    csv_path = output / "local_profile.csv"
    fields = ["mode", "trial", "elapsed_seconds", "peak_rss_mb", "rows", "unique_skills", "unique_pairs", "unique_triples", "skill_occurrences", "pair_occurrences", "triple_occurrences"]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)
    print(f"Профиль сохранён: {csv_path}")
    for mode in ["baseline", "optimized"]:
        subset = [r for r in records if r["mode"] == mode]
        mean = sum(r["elapsed_seconds"] for r in subset) / len(subset)
        print(f"{mode}: mean={mean:.4f}s; trials={[r['elapsed_seconds'] for r in subset]}; max RSS={max((r['peak_rss_mb'] or 0) for r in subset):.2f} MB")

if __name__ == "__main__":
    main()