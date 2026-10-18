"""Private administrator export. Reads credentials from Streamlit's secrets file."""

import argparse
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from storage import StorageError, export_csv  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--secrets", type=Path, default=ROOT / ".streamlit" / "secrets.toml")
    parser.add_argument("--output", type=Path, default=ROOT / "results.csv")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; choose a new name to preserve the previous export.")
    try:
        with args.secrets.open("rb") as source:
            config = tomllib.load(source)["supabase"]
        content = export_csv(config["url"], config["secret_key"])
    except (OSError, KeyError, ValueError, StorageError):
        parser.exit(1, "Export failed. Check the secrets file, database setup, and connection.\n")
    if not content:
        print("There are no submitted votes to export yet.")
        return
    args.output.write_text(content, encoding="utf-8-sig", newline="")
    print(f"Exported submitted votes to {args.output.name}.")


if __name__ == "__main__":
    main()
