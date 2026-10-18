"""Index existing images without changing them. Verify prompt mapping explicitly."""

import argparse
import csv
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from study import METHODS  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "image_manifest.csv")
    parser.add_argument("--confirm-filename-matches", action="store_true",
                        help="Confirm identical numeric stems mean identical original prompts across methods/resolutions.")
    parser.add_argument("--prompts", type=Path, help="Optional CSV containing prompt_id,prompt.")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; choose a new --output to preserve curated mappings.")
    prompts = {}
    if args.prompts:
        with args.prompts.open(encoding="utf-8-sig", newline="") as source:
            for row in csv.DictReader(source):
                prompts[row["prompt_id"]] = row["prompt"]
    fields = ["method", "resolution", "prompt_id", "image_id", "prompt", "path", "verified", "sha256"]
    rows = []
    for method in METHODS:
        for path in sorted((ROOT / "images" / method).glob("*/*.png")):
            if not path.parent.name.isdigit():
                parser.error(f"Non-numeric resolution directory: {path.parent}")
            rows.append({"method": method, "resolution": int(path.parent.name),
                         "prompt_id": path.stem, "image_id": path.stem,
                         "prompt": prompts.get(path.stem, ""), "path": path.relative_to(ROOT).as_posix(),
                         "verified": str(args.confirm_filename_matches).lower(),
                         "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    with args.output.open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Indexed {len(rows)} images in {args.output.name}. Verified matches: {args.confirm_filename_matches}.")


if __name__ == "__main__":
    main()
