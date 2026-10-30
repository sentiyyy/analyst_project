"""Run repeated end-to-end profiles and save wall time / peak RSS measurements."""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import psutil
from dotenv import load_dotenv

from src.s3io import upload_file, upload_json

load_dotenv()

def timed_run(label: str, command: list[str], output_dir: Path, trial: int) -> dict:
    started = time.perf_counter()
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    peak_rss = 0
    proc = psutil.Process(process.pid)
    while process.poll() is None:
        try:
            peak_rss = max(peak_rss, proc.memory_info().rss)
        except psutil.NoSuchProcess:
            pass
        time.sleep(0.05)
    output, _ = process.communicate()
    elapsed = time.perf_counter() - started
    if process.returncode != 0:
        raise RuntimeError(f"Профилирование {label} завершилось с кодом {process.returncode}.\n{output}")
    print(output, end="" if output.endswith("\n") else "\n")
    return {
        "method": label,
        "trial": trial,
        "elapsed_seconds": round(elapsed, 4),
        "sampled_peak_rss_mb": round(peak_rss / (1024 * 1024), 2),
        "command": " ".join(command),
    }

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--chunk-size", type=int, default=5000)
    parser.add_argument("--output-dir", default="results")
    parser.add_argument("--bucket", default=os.getenv("S3_BUCKET", "hh-lake"))
    args = parser.parse_args()
    if args.trials < 3:
        raise SystemExit("По требованиям задания нужно не менее трёх попыток.")
    output_dir = Path(args.output_dir)
    profile_dir = output_dir / "profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for label, extra in [
        ("MapReduce baseline (без кэша нормализации)", ["--no-cache"]),
        ("MapReduce optimized (кэш нормализации)", []),
        ("DuckDB SQL", ["--skip-business"]),
    ]:
        for trial in range(1, args.trials + 1):
            if label.startswith("DuckDB"):
                command = [sys.executable, "-m", "src.duckdb_compare", "--sizes", "all",
                           "--output-dir", str(profile_dir)] + extra
            else:
                command = [sys.executable, "-m", "src.mapreduce_s3", "--sizes", "all",
                           "--chunk-size", str(args.chunk_size), "--output-dir", str(profile_dir)] + extra
            records.append(timed_run(label, command, profile_dir, trial))
    csv_path = output_dir / "profile_runs.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["method", "trial", "elapsed_seconds", "sampled_peak_rss_mb", "command"])
        writer.writeheader()
        writer.writerows(records)
    summaries = {}
    for label in sorted({r["method"] for r in records}):
        subset = [r for r in records if r["method"] == label]
        summaries[label] = {
            "trials": len(subset),
            "mean_seconds": round(sum(r["elapsed_seconds"] for r in subset) / len(subset), 4),
            "min_seconds": min(r["elapsed_seconds"] for r in subset),
            "max_seconds": max(r["elapsed_seconds"] for r in subset),
            "max_sampled_peak_rss_mb": max(r["sampled_peak_rss_mb"] for r in subset),
        }
    summary_path = output_dir / "profile_summary.json"
    summary_path.write_text(json.dumps(summaries, ensure_ascii=False, indent=2), encoding="utf-8")
    bucket = args.bucket
    upload_file(csv_path, bucket, "results/analyst/profile/profile_runs.csv", "text/csv; charset=utf-8")
    upload_json(summaries, bucket, "results/analyst/profile/profile_summary.json")
    print(json.dumps(summaries, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()