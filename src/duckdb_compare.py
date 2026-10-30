from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path

import duckdb
from dotenv import load_dotenv

from src.s3io import upload_file, upload_json

load_dotenv()

DEFAULT_BUCKET = os.getenv("S3_BUCKET", "hh-lake")
DEFAULT_KEY = os.getenv("S3_SOURCE_KEY", "data/it_vacancies_2024.csv")
DUCKDB_ANALYST_ROLES = (
    "Аналитик",
    "Системный аналитик",
    "Бизнес-аналитик",
    "BI-аналитик, аналитик данных",
    "Продуктовый аналитик",
    "Руководитель отдела аналитики",
)

def sql_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"

def export_query(con, query: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    target = str(path.resolve()).replace("'", "''")
    con.execute(f"COPY ({query}) TO '{target}' (HEADER, DELIMITER ',')")

def count_csv_rows(path: Path) -> int:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return sum(1 for _ in csv.DictReader(handle))

def upload_outputs(output_dir: Path, bucket: str, prefix: str) -> None:
    for path in output_dir.rglob("*"):
        if path.is_file():
            rel = path.relative_to(output_dir).as_posix()
            upload_file(path, bucket, f"{prefix}/{rel}", "text/csv; charset=utf-8" if path.suffix == ".csv" else "application/json")

def create_base(con, bucket: str, key: str) -> None:
    endpoint = os.getenv("S3_ENDPOINT", "http://localhost:9000").replace("http://", "").replace("https://", "").rstrip("/")
    access = os.getenv("S3_ACCESS_KEY", "")
    secret = os.getenv("S3_SECRET_KEY", "")
    region = os.getenv("S3_REGION", "us-east-1")
    if not access or not secret:
        raise RuntimeError("Задайте S3_ACCESS_KEY и S3_SECRET_KEY в .env.")
    con.execute("INSTALL httpfs")
    con.execute("LOAD httpfs")
    con.execute("SET preserve_insertion_order = true")
    con.execute("SET threads = 1")
    con.execute(
        "CREATE OR REPLACE SECRET rustfs_secret (TYPE s3, PROVIDER config, "
        f"KEY_ID {sql_quote(access)}, SECRET {sql_quote(secret)}, REGION {sql_quote(region)}, "
        f"ENDPOINT {sql_quote(endpoint)}, URL_STYLE 'path', USE_SSL {'true' if os.getenv('S3_USE_SSL','false').lower() == 'true' else 'false'})"
    )
    roles = ", ".join(
        sql_quote(role) for role in DUCKDB_ANALYST_ROLES
    )
    uri = f"s3://{bucket}/{key}"
    con.execute(
        f"""CREATE OR REPLACE TEMP TABLE analyst_base AS
        WITH raw AS (
            SELECT
                row_number() OVER (ORDER BY ordinality) AS seq,
                trim(coalesce(vacancy_id, '')) AS vacancy_id,
                trim(coalesce(area, '')) AS area,
                trim(coalesce(name, '')) AS name,
                trim(coalesce(role, '')) AS role,
                trim(coalesce(currency, '')) AS currency,
                CASE
                    WHEN upper(trim(coalesce(currency, ''))) <> 'RUR' THEN NULL
                    WHEN TRY_CAST(NULLIF(trim(coalesce(salary_from, '')), '') AS DOUBLE) IS NOT NULL
                     AND TRY_CAST(NULLIF(trim(coalesce(salary_to, '')), '') AS DOUBLE) IS NOT NULL
                        THEN (TRY_CAST(NULLIF(trim(salary_from), '') AS DOUBLE) + TRY_CAST(NULLIF(trim(salary_to), '') AS DOUBLE)) / 2.0
                    ELSE COALESCE(
                        TRY_CAST(NULLIF(trim(coalesce(salary_from, '')), '') AS DOUBLE),
                        TRY_CAST(NULLIF(trim(coalesce(salary_to, '')), '') AS DOUBLE)
                    )
                END AS salary_raw,
                list_sort(list_distinct(list_filter(
                    list_transform(
                        string_split(coalesce(key_skills, ''), ';'),
                        lambda x: lower(regexp_replace(trim(x), '\\s+', ' ', 'g'))
                    ),
                    lambda x: x <> ''
                ))) AS skills
            FROM read_csv({sql_quote(uri)}, header=true, all_varchar=true, ignore_errors=true)
                 WITH ORDINALITY AS src
            WHERE trim(coalesce(role, '')) IN ({roles})
        )
        SELECT *, CASE WHEN salary_raw BETWEEN 10000 AND 1000000 THEN salary_raw ELSE NULL END AS salary
        FROM raw"""
    )

def count_query(size: str) -> str:
    return "SELECT * FROM analyst_base" if size == "all" else f"SELECT * FROM analyst_base WHERE seq <= {int(size)}"

def write_size_outputs(con, size: str, output_dir: Path, bucket: str, upload: bool) -> dict:
    subset = f"({count_query(size)}) AS b"
    target = output_dir / "duckdb" / size
    skills_query = f"""SELECT skill, count(*) AS count
        FROM {subset}, UNNEST(skills) AS s(skill)
        GROUP BY skill ORDER BY skill"""
    pairs_query = f"""SELECT list_extract(skills, i) AS skill_1,
            list_extract(skills, j) AS skill_2, count(*) AS count
        FROM {subset},
             UNNEST(range(1, len(skills) + 1)) AS ix(i),
             UNNEST(range(1, len(skills) + 1)) AS jx(j)
        WHERE i < j
        GROUP BY 1, 2 ORDER BY 1, 2"""
    triples_query = f"""SELECT list_extract(skills, i) AS skill_1,
            list_extract(skills, j) AS skill_2,
            list_extract(skills, k) AS skill_3, count(*) AS count
        FROM {subset},
             UNNEST(range(1, len(skills) + 1)) AS ix(i),
             UNNEST(range(1, len(skills) + 1)) AS jx(j),
             UNNEST(range(1, len(skills) + 1)) AS kx(k)
        WHERE i < j AND j < k
        GROUP BY 1, 2, 3 ORDER BY 1, 2, 3"""
    export_query(con, skills_query, target / "skills.csv")
    export_query(con, pairs_query, target / "pairs.csv")
    export_query(con, triples_query, target / "triples.csv")
    stats = con.execute(
        f"""SELECT count(*) AS analyst_rows,
            sum(CASE WHEN len(skills) > 0 THEN 1 ELSE 0 END) AS vacancies_with_skills,
            coalesce(sum(len(skills)), 0) AS skill_occurrences,
            coalesce(sum(len(skills) * (len(skills)-1) / 2), 0) AS pair_occurrences,
            coalesce(sum(len(skills) * (len(skills)-1) * (len(skills)-2) / 6), 0) AS triple_occurrences
            FROM {subset}"""
    ).fetchone()
    # Count exported rows in a streaming pass rather than recomputing the large SQL group-bys.
    unique_skills = count_csv_rows(target / "skills.csv")
    unique_pairs = count_csv_rows(target / "pairs.csv")
    unique_triples = count_csv_rows(target / "triples.csv")
    summary = {
        "method": "duckdb_sql_s3",
        "variant": "10. Аналитик",
        "size": size,
        "analyst_rows": int(stats[0] or 0),
        "vacancies_with_at_least_one_skill": int(stats[1] or 0),
        "skill_occurrences": int(stats[2] or 0),
        "pair_occurrences": int(stats[3] or 0),
        "triple_occurrences": int(stats[4] or 0),
        "unique_skills": int(unique_skills),
        "unique_pairs": int(unique_pairs),
        "unique_triples": int(unique_triples),
    }
    (target / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    if upload:
        upload_outputs(target, bucket, f"results/analyst/duckdb/{size}")
        upload_json(summary, bucket, f"results/analyst/duckdb/{size}/summary.json")
    print(json.dumps(summary, ensure_ascii=False))
    return summary

def run_business_analysis(con, output_dir: Path, bucket: str, upload: bool) -> None:
    out = output_dir / "duckdb" / "business"
    out.mkdir(parents=True, exist_ok=True)
    con.execute("""CREATE OR REPLACE TEMP TABLE all_skill_counts AS
        SELECT skill, count(*) AS vacancy_count
        FROM analyst_base, UNNEST(skills) AS s(skill)
        GROUP BY skill""")
    con.execute("""CREATE OR REPLACE TEMP TABLE salary_skill_stats AS
        SELECT skill, count(*) AS salary_n, median(salary) AS median_salary
        FROM analyst_base, UNNEST(skills) AS s(skill)
        WHERE salary IS NOT NULL
        GROUP BY skill HAVING count(*) >= 20""")
    con.execute("""CREATE OR REPLACE TEMP TABLE all_pair_counts AS
        SELECT list_extract(skills, i) AS skill_1, list_extract(skills, j) AS skill_2,
               count(*) AS vacancy_count
        FROM analyst_base,
             UNNEST(range(1, len(skills) + 1)) AS ix(i),
             UNNEST(range(1, len(skills) + 1)) AS jx(j)
        WHERE i < j GROUP BY 1, 2""")
    con.execute("""CREATE OR REPLACE TEMP TABLE salary_pair_stats AS
        SELECT list_extract(skills, i) AS skill_1, list_extract(skills, j) AS skill_2,
               count(*) AS salary_n, median(salary) AS median_salary
        FROM analyst_base,
             UNNEST(range(1, len(skills) + 1)) AS ix(i),
             UNNEST(range(1, len(skills) + 1)) AS jx(j)
        WHERE salary IS NOT NULL AND i < j
        GROUP BY 1, 2 HAVING count(*) >= 30""")
    con.execute("""CREATE OR REPLACE TEMP TABLE salary_triple_stats AS
        SELECT list_extract(skills, i) AS skill_1, list_extract(skills, j) AS skill_2,
               list_extract(skills, k) AS skill_3, count(*) AS salary_n, median(salary) AS median_salary
        FROM analyst_base,
             UNNEST(range(1, len(skills) + 1)) AS ix(i),
             UNNEST(range(1, len(skills) + 1)) AS jx(j),
             UNNEST(range(1, len(skills) + 1)) AS kx(k)
        WHERE salary IS NOT NULL AND i < j AND j < k
        GROUP BY 1, 2, 3 HAVING count(*) >= 20""")
    con.execute("""CREATE OR REPLACE TEMP TABLE pair_uplift AS
        SELECT p.skill_1, p.skill_2, p.salary_n, p.median_salary,
               CASE WHEN c1.vacancy_count >= c2.vacancy_count THEN p.skill_1 ELSE p.skill_2 END AS baseline_skill,
               CASE WHEN c1.vacancy_count >= c2.vacancy_count THEN s1.median_salary ELSE s2.median_salary END AS baseline_median,
               p.median_salary - CASE WHEN c1.vacancy_count >= c2.vacancy_count THEN s1.median_salary ELSE s2.median_salary END AS delta,
               100.0 * (p.median_salary - CASE WHEN c1.vacancy_count >= c2.vacancy_count THEN s1.median_salary ELSE s2.median_salary END)
                    / NULLIF(CASE WHEN c1.vacancy_count >= c2.vacancy_count THEN s1.median_salary ELSE s2.median_salary END, 0) AS pct_change
        FROM salary_pair_stats p
        JOIN all_skill_counts c1 ON c1.skill = p.skill_1
        JOIN all_skill_counts c2 ON c2.skill = p.skill_2
        JOIN salary_skill_stats s1 ON s1.skill = p.skill_1
        JOIN salary_skill_stats s2 ON s2.skill = p.skill_2""")
    con.execute("""CREATE OR REPLACE TEMP TABLE triple_uplift AS
        WITH base AS (
            SELECT t.*,
                p12.vacancy_count AS f12, p13.vacancy_count AS f13, p23.vacancy_count AS f23,
                s12.median_salary AS m12, s13.median_salary AS m13, s23.median_salary AS m23
            FROM salary_triple_stats t
            JOIN all_pair_counts p12 ON p12.skill_1=t.skill_1 AND p12.skill_2=t.skill_2
            JOIN all_pair_counts p13 ON p13.skill_1=t.skill_1 AND p13.skill_2=t.skill_3
            JOIN all_pair_counts p23 ON p23.skill_1=t.skill_2 AND p23.skill_2=t.skill_3
            LEFT JOIN salary_pair_stats s12 ON s12.skill_1=t.skill_1 AND s12.skill_2=t.skill_2
            LEFT JOIN salary_pair_stats s13 ON s13.skill_1=t.skill_1 AND s13.skill_2=t.skill_3
            LEFT JOIN salary_pair_stats s23 ON s23.skill_1=t.skill_2 AND s23.skill_2=t.skill_3
        ), chosen AS (
            SELECT *,
                CASE WHEN f12 >= f13 AND f12 >= f23 THEN skill_1 || ' + ' || skill_2
                     WHEN f13 >= f12 AND f13 >= f23 THEN skill_1 || ' + ' || skill_3
                     ELSE skill_2 || ' + ' || skill_3 END AS baseline_pair,
                CASE WHEN f12 >= f13 AND f12 >= f23 THEN m12
                     WHEN f13 >= f12 AND f13 >= f23 THEN m13
                     ELSE m23 END AS baseline_median
            FROM base
        )
        SELECT skill_1, skill_2, skill_3, salary_n, median_salary, baseline_pair, baseline_median,
               median_salary - baseline_median AS delta,
               100.0 * (median_salary - baseline_median) / NULLIF(baseline_median, 0) AS pct_change
        FROM chosen WHERE baseline_median IS NOT NULL""")

    region_summary = """SELECT area, count(*) AS vacancies,
        sum(CASE WHEN len(skills)>0 THEN 1 ELSE 0 END) AS skill_vacancies,
        count(salary) AS salary_n, median(salary) AS median_salary
        FROM analyst_base WHERE trim(area) <> ''
        GROUP BY area ORDER BY vacancies DESC LIMIT 3"""
    export_query(con, region_summary, out / "region_summary.csv")
    regions = "(SELECT area FROM analyst_base WHERE trim(area)<>'' GROUP BY area ORDER BY count(*) DESC LIMIT 3)"
    region_skills = f"""WITH counts AS (
        SELECT area, skill, count(*) AS frequency
        FROM analyst_base, UNNEST(skills) AS s(skill)
        WHERE area IN {regions} GROUP BY area, skill
    ), ranked AS (
        SELECT *, row_number() OVER (PARTITION BY area ORDER BY frequency DESC, skill) AS rn FROM counts
    ) SELECT area, skill, frequency FROM ranked WHERE rn <= 5 ORDER BY area, rn"""
    region_pairs = f"""WITH counts AS (
        SELECT area, list_extract(skills, i) AS skill_1, list_extract(skills, j) AS skill_2, count(*) AS frequency
        FROM analyst_base,
             UNNEST(range(1, len(skills) + 1)) AS ix(i),
             UNNEST(range(1, len(skills) + 1)) AS jx(j)
        WHERE area IN {regions} AND i < j GROUP BY area, skill_1, skill_2
    ), ranked AS (
        SELECT *, row_number() OVER (PARTITION BY area ORDER BY frequency DESC, skill_1, skill_2) AS rn FROM counts
    ) SELECT area, skill_1, skill_2, frequency FROM ranked WHERE rn <= 5 ORDER BY area, rn"""
    region_triples = f"""WITH counts AS (
        SELECT area, list_extract(skills, i) AS skill_1, list_extract(skills, j) AS skill_2,
               list_extract(skills, k) AS skill_3, count(*) AS frequency
        FROM analyst_base,
             UNNEST(range(1, len(skills) + 1)) AS ix(i),
             UNNEST(range(1, len(skills) + 1)) AS jx(j),
             UNNEST(range(1, len(skills) + 1)) AS kx(k)
        WHERE area IN {regions} AND i < j AND j < k GROUP BY area, skill_1, skill_2, skill_3
    ), ranked AS (
        SELECT *, row_number() OVER (PARTITION BY area ORDER BY frequency DESC, skill_1, skill_2, skill_3) AS rn FROM counts
    ) SELECT area, skill_1, skill_2, skill_3, frequency FROM ranked WHERE rn <= 5 ORDER BY area, rn"""
    for query, name in [
        (region_skills, "region_top_skills.csv"),
        (region_pairs, "region_top_pairs.csv"),
        (region_triples, "region_top_triples.csv"),
    ]:
        export_query(con, query, out / name)

    pair_cols = "skill_1, skill_2, salary_n, median_salary, baseline_skill, baseline_median, delta, pct_change"
    triple_cols = "skill_1, skill_2, skill_3, salary_n, median_salary, baseline_pair, baseline_median, delta, pct_change"
    export_query(con, f"SELECT {pair_cols} FROM pair_uplift ORDER BY delta DESC LIMIT 10", out / "salary_pairs_positive.csv")
    export_query(con, f"SELECT {pair_cols} FROM pair_uplift ORDER BY delta ASC LIMIT 10", out / "salary_pairs_negative.csv")
    export_query(con, f"SELECT {triple_cols} FROM triple_uplift ORDER BY delta DESC LIMIT 10", out / "salary_triples_positive.csv")
    export_query(con, f"SELECT {triple_cols} FROM triple_uplift ORDER BY delta ASC LIMIT 10", out / "salary_triples_negative.csv")

    meta = con.execute("""SELECT count(*) AS analyst_rows,
        sum(CASE WHEN len(skills)>0 THEN 1 ELSE 0 END) AS skill_rows,
        count(salary) AS salary_rows,
        median(salary) AS median_salary
        FROM analyst_base""").fetchone()
    summary = {
        "variant": "10. Аналитик",
        "analyst_rows": int(meta[0] or 0),
        "vacancies_with_at_least_one_skill": int(meta[1] or 0),
        "salary_rows_after_currency_and_range_filter": int(meta[2] or 0),
        "median_salary_rur": float(meta[3]) if meta[3] is not None else None,
        "salary_method": "RUR only; midpoint when both bounds exist; otherwise available bound; keep 10,000–1,000,000 RUR",
        "pair_salary_min_support": 30,
        "triple_salary_min_support": 20,
        "single_skill_salary_min_support": 20,
        "regions": "top 3 by number of analyst vacancies",
    }
    (out / "business_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    if upload:
        upload_outputs(out, bucket, "results/analyst/business")
        upload_json(summary, bucket, "results/analyst/business/business_summary.json")
    print(json.dumps(summary, ensure_ascii=False))

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", default="1000,5000,10000,all")
    parser.add_argument("--bucket", default=DEFAULT_BUCKET)
    parser.add_argument("--key", default=DEFAULT_KEY)
    parser.add_argument("--output-dir", default="results")
    parser.add_argument("--no-upload", action="store_true")
    parser.add_argument("--skip-business", action="store_true", help="Skip salary and regional analysis for profiling runs")
    args = parser.parse_args()
    output_dir = Path(args.output_dir)
    con = duckdb.connect(database=":memory:")
    try:
        create_base(con, args.bucket, args.key)
        summaries = []
        for size in [x.strip() for x in args.sizes.split(",") if x.strip()]:
            if size != "all" and (not size.isdigit() or int(size) < 1):
                raise SystemExit(f"Некорректный размер выборки: {size}")
            summaries.append(write_size_outputs(con, size, output_dir, args.bucket, not args.no_upload))
        summary_path = output_dir / "duckdb" / "summaries.json"
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(summaries, ensure_ascii=False, indent=2), encoding="utf-8")
        if not args.no_upload:
            upload_file(summary_path, args.bucket, "results/analyst/duckdb/summaries.json", "application/json")
        if not args.skip_business:
            run_business_analysis(con, output_dir, args.bucket, not args.no_upload)
    finally:
        con.close()

if __name__ == "__main__":
    main()