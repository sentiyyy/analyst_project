"""Create the hh-lake bucket if needed and upload the source CSV to RustFS."""
from __future__ import annotations

import argparse
from pathlib import Path

from dotenv import load_dotenv

from src.s3io import ensure_bucket, s3_client

load_dotenv()

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", default="data/it_vacancies_2024.csv")
    parser.add_argument("--bucket", default="hh-lake")
    parser.add_argument("--key", default="data/it_vacancies_2024.csv")
    args = parser.parse_args()
    path = Path(args.file)
    if not path.is_file():
        raise SystemExit(f"Файл не найден: {path}. Сначала выполните src.prepare_data.")
    client = s3_client()
    ensure_bucket(client, args.bucket)
    client.upload_file(str(path), args.bucket, args.key, ExtraArgs={"ContentType": "text/csv; charset=utf-8"})
    head = client.head_object(Bucket=args.bucket, Key=args.key)
    print(f"Загружено: s3://{args.bucket}/{args.key}; размер={head['ContentLength']:,} байт")

if __name__ == "__main__":
    main()