"""Extract the CSV from the supplied ZIP archive without loading it into RAM."""
from __future__ import annotations

import argparse
import shutil
import zipfile
from pathlib import Path

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, help="Path to it_vacancies_2024.zip")
    parser.add_argument("--output", default="data/it_vacancies_2024.csv")
    args = parser.parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.archive) as archive:
        candidates = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if not candidates:
            raise SystemExit("В архиве не найден CSV-файл.")
        if len(candidates) > 1:
            print(f"В архиве несколько CSV; используется первый: {candidates[0]}")
        with archive.open(candidates[0]) as source, output.open("wb") as target:
            shutil.copyfileobj(source, target, length=8 * 1024 * 1024)
    print(f"CSV сохранён: {output} ({output.stat().st_size:,} байт)")


if __name__ == "__main__":
    main()