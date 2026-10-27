"""Validate, clean and consolidate the daily branch transaction files."""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
FILE_PATTERN = "transactions_*.csv"

# Columns every daily file must have (after known aliases are mapped).
STANDARD_COLUMNS = [
    "transaction_id", "date", "branch", "customer_id", "product",
    "quantity", "unit_price", "payment_method", "payment_ref",
]
REQUIRED_COLUMNS = set(STANDARD_COLUMNS)
# payment_ref is a required column but its value is blank for cash sales.
OPTIONAL_VALUES = {"payment_ref"}
NUMERIC_FIELDS = {"quantity", "unit_price"}

# Known column renames from branch POS systems: old/alternative name -> standard name.
COLUMN_ALIASES = {"qty": "quantity"}

ACCEPTED_COLUMNS = [
    "transaction_id", "date", "branch", "customer_id", "product",
    "quantity", "unit_price", "line_total", "payment_method", "payment_ref",
    "source_file",
]
# Rejected file: these four first, then the original columns of the row.
REJECTED_LEAD_COLUMNS = ["source_file", "row_number", "transaction_id", "reasons"]


# Task 1: read one file
def read_transactions(path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    """Read one CSV into its header and a list of row dicts.

    Each row gets "_source_file" and "_row_number" (the line in the file,
    where the header is line 1) so rejected rows can be traced back.
    """
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        header = [name.strip() for name in (reader.fieldnames or [])]
        reader.fieldnames = header
        for row in reader:
            row["_source_file"] = path.name
            row["_row_number"] = reader.line_num
            rows.append(row)
    return header, rows


# Task 2: discover files
def file_date(path: Path) -> date | None:
    """Return the date in a name like transactions_2026-10-01.csv, or None."""
    try:
        return date.fromisoformat(path.stem.split("_", 1)[1])
    except (IndexError, ValueError):
        return None


def discover_files(folder: Path, pattern: str = FILE_PATTERN) -> list[Path]:
    """Return every daily file matching pattern, oldest date first.

    Files whose name does not contain a YYYY-MM-DD date are left out.
    """
    dated = [path for path in Path(folder).glob(pattern) if path.is_file() and file_date(path)]
    return sorted(dated, key=file_date)


# Task 3: validate columns
def validate_columns(header: list[str], required: set[str] = REQUIRED_COLUMNS) -> dict[str, Any]:
    """Compare a header with the required columns using set operations.

    Known aliases (e.g. qty) are mapped to the standard name first.
    """
    renamed = {col: COLUMN_ALIASES[col] for col in header if col in COLUMN_ALIASES}
    columns = {renamed.get(col, col) for col in header}
    missing = required - columns
    extra = columns - required
    return {
        "ok": not missing,
        "missing": sorted(missing),
        "extra": sorted(extra),
        "renamed": renamed,
    }


def apply_aliases(row: dict[str, Any], renamed: dict[str, str]) -> dict[str, Any]:
    return {renamed.get(key, key): value for key, value in row.items()}


# Task 4: find every problem with a row
def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def find_problems(row: dict[str, Any], seen_ids: dict[str, str]) -> list[str]:
    """Return every reason the row is invalid (empty list means it is valid).

    seen_ids maps each transaction_id already accepted in this run to
    where it was accepted, e.g. "transactions_2026-10-01.csv row 13".
    """
    problems = []

    if None in row:
        problems.append("row has more fields than the header")

    for field in sorted(REQUIRED_COLUMNS - OPTIONAL_VALUES):
        if not _text(row.get(field)):
            problems.append(f"missing {field}")

    for field in sorted(NUMERIC_FIELDS):
        value = _text(row.get(field))
        if not value:
            continue  # already reported as missing
        try:
            number = int(value)
        except ValueError:
            problems.append(f"{field} is not a whole number ('{value}')")
            continue
        if number <= 0:
            problems.append(f"{field} must be positive ({number})")

    date_text = _text(row.get("date"))
    if date_text:
        try:
            date.fromisoformat(date_text)
        except ValueError:
            problems.append(f"date is not YYYY-MM-DD ('{date_text}')")

    txn_id = _text(row.get("transaction_id"))
    if txn_id in seen_ids:
        problems.append(f"duplicate transaction_id (already accepted from {seen_ids[txn_id]})")

    return problems


# Task 5: clean a valid row
def clean_row(row: dict[str, Any]) -> dict[str, Any]:
    """Strip whitespace, convert numbers to int and add line_total."""
    keep = set(ACCEPTED_COLUMNS) - {"line_total", "source_file"}
    cleaned = {key: _text(value) for key, value in row.items() if key in keep}
    for field in NUMERIC_FIELDS:
        cleaned[field] = int(cleaned[field])
    cleaned["line_total"] = cleaned["quantity"] * cleaned["unit_price"]
    cleaned["source_file"] = row["_source_file"]
    return cleaned


def make_rejected(row: dict[str, Any], reasons: list[str]) -> dict[str, Any]:
    """Keep every original value of the row and add where it came from and why it failed."""
    rejected = {
        key: _text(value) for key, value in row.items()
        if key is not None and not key.startswith("_")
    }
    rejected["source_file"] = row["_source_file"]
    rejected["row_number"] = row["_row_number"]
    rejected["reasons"] = "; ".join(reasons)
    return rejected


def rejected_columns(extra_columns: set[str]) -> list[str]:
    """Lead columns, then the standard columns, then any extra columns seen (e.g. cashier)."""
    original = [col for col in STANDARD_COLUMNS if col != "transaction_id"]
    return REJECTED_LEAD_COLUMNS + original + sorted(extra_columns)


# Tasks 6 and 7: write the outputs
def write_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def process_files(files: list[Path]) -> tuple[list[dict], list[dict], dict[str, Any]]:
    """Run every file through validation and cleaning."""
    accepted: list[dict] = []
    rejected: list[dict] = []
    seen_ids: dict[str, str] = {}
    stats: dict[str, Any] = {
        "files_discovered": len(files),
        "files_processed": 0,
        "column_issues": [],
        "extra_columns": set(),
        "rows_read": 0,
    }

    for path in files:
        try:
            header, rows = read_transactions(path)
        except (OSError, UnicodeDecodeError, csv.Error) as exc:
            stats["column_issues"].append(f"{path.name}: could not be read ({exc})")
            continue

        stats["rows_read"] += len(rows)
        if not header:
            stats["column_issues"].append(f"{path.name}: empty file, no header")
            continue
        check = validate_columns(header)

        if not check["ok"]:
            # Without a required column no row in this file can be trusted.
            missing = ", ".join(check["missing"])
            stats["column_issues"].append(f"{path.name}: missing {missing}, whole file rejected")
            reason = f"file is missing required columns: {missing}"
            for row in rows:
                rejected.append(make_rejected(row, [reason]))
            continue

        if check["renamed"] or check["extra"]:
            notes = [f"missing {new} (mapped from {old})" for old, new in check["renamed"].items()]
            notes += [f"extra {col}" for col in check["extra"]]
            stats["column_issues"].append(f"{path.name}: {', '.join(notes)}")
        stats["extra_columns"].update(check["extra"])
        stats["files_processed"] += 1

        for row in rows:
            row = apply_aliases(row, check["renamed"])
            problems = find_problems(row, seen_ids)
            if problems:
                rejected.append(make_rejected(row, problems))
            else:
                clean = clean_row(row)
                seen_ids[clean["transaction_id"]] = f"{path.name} row {row['_row_number']}"
                accepted.append(clean)

    stats["accepted"] = len(accepted)
    stats["rejected"] = len(rejected)
    if stats["accepted"] + stats["rejected"] != stats["rows_read"]:
        raise RuntimeError("Row counts do not reconcile: accepted + rejected != rows read.")
    return accepted, rejected, stats


# Task 8: summary
def reason_category(reason: str) -> str:
    """Put a rejection reason into one of the summary's groups."""
    if reason.startswith("duplicate"):
        return "duplicate IDs"
    if reason.startswith("missing "):
        return "missing values"
    if "whole number" in reason or "must be positive" in reason:
        return "invalid numbers"
    return "other"


def build_summary(stats: dict[str, Any]) -> str:
    """Build the processing summary in the layout from expected_output.md.

    The breakdown counts rows: a row with reasons in two groups is counted
    in both, so the groups can add up to more than Rows rejected.
    """
    groups = Counter(
        category
        for row in stats["rejected_rows"]
        for category in {reason_category(r) for r in row["reasons"].split("; ")}
    )
    revenue = sum(row["line_total"] for row in stats["accepted_rows"])

    issues = stats["column_issues"]
    issues_text = f"{len(issues)}  ({'; '.join(issues)})" if issues else "0"

    def line(label: str, value: Any) -> str:
        return f"{label:<25}{value}"

    lines = [
        "=== Processing summary ===",
        line("Files discovered:", stats["files_discovered"]),
        line("Files processed:", stats["files_processed"]),
        line("Files with column issues:", issues_text),
        line("Rows read:", stats["rows_read"]),
        line("Rows accepted:", stats["accepted"]),
        line("Rows rejected:", stats["rejected"]),
        line("  - missing values:", groups["missing values"]),
        line("  - invalid numbers:", groups["invalid numbers"]),
        line("  - duplicate IDs:", groups["duplicate IDs"]),
    ]
    if groups["other"]:
        lines.append(line("  - other:", groups["other"]))
    lines += [
        line("Total revenue (KES):", f"{revenue:,}"),
        line("Run finished at:", stats["run_at"]),
    ]
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DATA_DIR, help="folder with the daily files (default: data/)")
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR, help="output folder (default: output/)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.data.is_dir():
        print(f"ERROR: data folder not found: {args.data}", file=sys.stderr)
        return 1

    files = discover_files(args.data)
    if not files:
        print(f"ERROR: no {FILE_PATTERN} files found in {args.data}", file=sys.stderr)
        return 1
    for path in sorted(set(args.data.glob(FILE_PATTERN)) - set(files)):
        print(f"WARNING: skipped {path.name}, no YYYY-MM-DD date in the file name", file=sys.stderr)

    consolidated_path = args.output / "consolidated_transactions.csv"
    rejected_path = args.output / "rejected_transactions.csv"
    summary_path = args.output / "summary.txt"

    try:
        accepted, rejected, stats = process_files(files)
        write_csv(consolidated_path, ACCEPTED_COLUMNS, accepted)
        write_csv(rejected_path, rejected_columns(stats["extra_columns"]), rejected)

        stats["accepted_rows"] = accepted
        stats["rejected_rows"] = rejected
        stats["run_at"] = datetime.now().isoformat(timespec="seconds")
        summary = build_summary(stats)
        summary_path.write_text(summary + "\n", encoding="utf-8")
    except (OSError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
