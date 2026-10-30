"""Run both independent counting implementations and verify exact agreement."""
from __future__ import annotations

import argparse
import subprocess
import sys

def run(command: list[str]) -> None:
    print("\n$", " ".join(command), flush=True)
    subprocess.run(command, check=True)

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", default="1000,5000,10000,all")
    parser.add_argument("--chunk-size", type=int, default=5000)
    parser.add_argument("--output-dir", default="results")
    args = parser.parse_args()
    run([sys.executable, "-m", "src.mapreduce_s3", "--sizes", args.sizes,
         "--chunk-size", str(args.chunk_size), "--output-dir", args.output_dir])
    run([sys.executable, "-m", "src.duckdb_compare", "--sizes", args.sizes,
         "--output-dir", args.output_dir])
    run([sys.executable, "-m", "src.verify_results", "--sizes", args.sizes,
         "--output-dir", args.output_dir])

if __name__ == "__main__":
    main()