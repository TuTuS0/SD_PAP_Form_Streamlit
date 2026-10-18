"""Merge downloaded Drive batch CSVs, preserving votes and detecting conflicts."""

import argparse
import csv
from pathlib import Path

REQUIRED_COLUMNS = {
    "participant_id", "batch_id", "batch_number", "comparison_number",
    "batch_comparison_number", "comparison_id", "prompt_id", "prompt", "target_resolution",
    "left_method", "right_method", "left_image_id", "right_image_id", "left_path", "right_path",
    "left_sha256", "right_sha256", "selected_side", "winner", "answered_at", "pool_cycle",
    "is_repeat", "repeat_reason", "dataset_version", "sampler_version", "sampling_seed",
    "submitted_at", "submission_sha256",
}


def merge_batches(paths: list[Path]) -> tuple[list[str], list[dict[str, str]]]:
    columns = None
    batches: dict[str, list[dict[str, str]]] = {}
    slots: dict[tuple[str, int], str] = {}
    for path in paths:
        with path.open(encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source)
            header = reader.fieldnames or []
            if set(header) != REQUIRED_COLUMNS or len(header) != len(REQUIRED_COLUMNS):
                raise ValueError(f"Unexpected CSV columns in {path.name}.")
            rows = list(reader)
        if len(rows) != 10 or any(None in row or any(value is None for value in row.values()) for row in rows):
            raise ValueError(f"Expected exactly 10 complete votes in {path.name}.")
        columns = columns or header
        first = rows[0]
        participant_id, batch_id = first["participant_id"], first["batch_id"]
        number = int(first["batch_number"])
        if not participant_id or not batch_id or number < 1:
            raise ValueError(f"Invalid batch identifiers in {path.name}.")
        shared = ("participant_id", "batch_id", "batch_number", "dataset_version", "sampler_version",
                  "sampling_seed", "submitted_at", "submission_sha256")
        if any(any(row[key] != first[key] for key in shared) for row in rows):
            raise ValueError(f"Mixed submission metadata in {path.name}.")
        for index, row in enumerate(rows, 1):
            if (int(row["batch_comparison_number"]) != index
                    or int(row["comparison_number"]) != (number - 1) * 10 + index
                    or row["selected_side"] not in {"left", "right"}
                    or row["winner"] != row[row["selected_side"] + "_method"]):
                raise ValueError(f"Invalid vote numbering or winner in {path.name}.")
        if len({row["comparison_id"] for row in rows}) != 10:
            raise ValueError(f"Duplicate comparisons in {path.name}.")
        previous = batches.get(batch_id)
        if previous is not None:
            if previous != rows:
                raise ValueError(f"Conflicting copies of a batch in {path.name}.")
            continue
        slot = (participant_id, number)
        if slot in slots:
            raise ValueError(f"Two different batches occupy the same submission number in {path.name}.")
        slots[slot] = batch_id
        batches[batch_id] = rows
    if columns is None:
        raise ValueError("No batch CSV files found.")
    combined = [row for rows in batches.values() for row in rows]
    combined.sort(key=lambda row: (row["participant_id"], int(row["comparison_number"])))
    return columns, combined


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Directory containing downloaded pixel_votes_*.csv files.")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; choose a new filename to preserve the previous export.")
    try:
        columns, rows = merge_batches(sorted(args.input.rglob("pixel_votes_*.csv")))
        with args.output.open("x", encoding="utf-8-sig", newline="") as target:
            writer = csv.DictWriter(target, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Merge failed: {exc}\n")
    print(f"Merged {len(rows)} votes from {len(rows) // 10} complete batches into {args.output.name}.")


if __name__ == "__main__":
    main()
