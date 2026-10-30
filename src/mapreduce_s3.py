"""Chunked MapReduce over the source CSV stored in RustFS/S3."""
from __future__ import annotations

import argparse
import csv
import io
import itertools
import json
import os
import time
try:
    import resource
except ImportError:  # Windows
    resource = None
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

from src.common import is_analyst, parse_skills
from src.s3io import s3_client, upload_file, upload_json

load_dotenv()

DEFAULT_BUCKET = os.getenv("S3_BUCKET", "hh-lake")
DEFAULT_KEY = os.getenv("S3_SOURCE_KEY", "data/it_vacancies_2024.csv")

def _write_counter(path: Path, fields: list[str], counter: Counter) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(fields + ["count"])
        for key, count in sorted(counter.items()):
            values = (key,) if isinstance(key, str) else tuple(key)
            writer.writerow([*values, count])

def run_one(size: str, chunk_size: int, bucket: str, key: str, output_dir: Path,
            upload_results: bool, use_cache: bool) -> dict:
    limit = None if size == "all" else int(size)
    started = time.perf_counter()
    selected_rows = 0
    skill_bearing_rows = 0
    source_rows_processed = 0
    map_tasks = 0
    skills_total = Counter()
    pairs_total = Counter()
    triples_total = Counter()
    client = s3_client()
    response = client.get_object(Bucket=bucket, Key=key)
    body = response["Body"]
    text = io.TextIOWrapper(body, encoding="utf-8-sig", newline="")
    reader = csv.DictReader(text)

    try:
        while True:
            raw_chunk = list(itertools.islice(reader, chunk_size))
            if not raw_chunk:
                break
            map_tasks += 1
            local_skills = Counter()
            local_pairs = Counter()
            local_triples = Counter()
            for row in raw_chunk:
                if limit is not None and selected_rows >= limit:
                    break
                source_rows_processed += 1
                if not is_analyst(row):
                    continue
                selected_rows += 1
                skills = parse_skills(row.get("key_skills", ""), use_cache=use_cache)
                if skills:
                    skill_bearing_rows += 1
                local_skills.update(skills)
                local_pairs.update(itertools.combinations(skills, 2))
                local_triples.update(itertools.combinations(skills, 3))
            skills_total.update(local_skills)
            pairs_total.update(local_pairs)
            triples_total.update(local_triples)
            if limit is not None and selected_rows >= limit:
                break
    finally:
        try:
            text.detach()
        except Exception:
            pass
        body.close()

    result_dir = output_dir / "mapreduce" / size
    _write_counter(result_dir / "skills.csv", ["skill"], skills_total)
    _write_counter(result_dir / "pairs.csv", ["skill_1", "skill_2"], pairs_total)
    _write_counter(result_dir / "triples.csv", ["skill_1", "skill_2", "skill_3"], triples_total)
    elapsed = time.perf_counter() - started
    if resource is not None:
        peak_rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    else:
        import psutil
        peak_rss_mb = psutil.Process().memory_info().rss / (1024 * 1024)
    summary = {
        "method": "python_mapreduce_s3",
        "variant": "10. Аналитик",
        "size": size,
        "analyst_rows": selected_rows,
        "source_rows_processed": source_rows_processed,
        "map_tasks": map_tasks,
        "chunk_size_source_rows": chunk_size,
        "vacancies_with_at_least_one_skill": skill_bearing_rows,
        "skill_occurrences": int(sum(skills_total.values())),
        "pair_occurrences": int(sum(pairs_total.values())),
        "triple_occurrences": int(sum(triples_total.values())),
        "unique_skills": len(skills_total),
        "unique_pairs": len(pairs_total),
        "unique_triples": len(triples_total),
        "normalization_cache": use_cache,
        "elapsed_seconds": round(elapsed, 4),
        "process_peak_rss_mb": round(peak_rss_mb, 2),
    }
    summary_path = result_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    if upload_results:
        prefix = f"results/analyst/mapreduce/{size}"
        for name in ("skills.csv", "pairs.csv", "triples.csv"):
            upload_file(result_dir / name, bucket, f"{prefix}/{name}", "text/csv; charset=utf-8")
        upload_json(summary, bucket, f"{prefix}/summary.json")
    print(json.dumps(summary, ensure_ascii=False))
    return summary

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", default="1000,5000,10000,all", help="Comma-separated analyst row limits or all")
    parser.add_argument("--chunk-size", type=int, default=5000, help="Raw source CSV rows per mapper chunk")
    parser.add_argument("--bucket", default=DEFAULT_BUCKET)
    parser.add_argument("--key", default=DEFAULT_KEY)
    parser.add_argument("--output-dir", default="results")
    parser.add_argument("--no-upload", action="store_true")
    parser.add_argument("--no-cache", action="store_true", help="Disable skill-normalization cache for baseline profiling")
    args = parser.parse_args()
    if args.chunk_size < 1:
        raise SystemExit("--chunk-size должен быть положительным целым числом.")
    sizes = [x.strip() for x in args.sizes.split(",") if x.strip()]
    for size in sizes:
        if size != "all" and (not size.isdigit() or int(size) < 1):
            raise SystemExit(f"Некорректный размер выборки: {size}")
    # Each size is a separate S3 streaming pass. This keeps only one final set
    # of Counters in memory and makes the first N matching records reproducible.
    for size in sizes:
        run_one(size, args.chunk_size, args.bucket, args.key, Path(args.output_dir),
                not args.no_upload, not args.no_cache)

if __name__ == "__main__":
    main()