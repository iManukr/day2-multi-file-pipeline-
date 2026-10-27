"""Validate, clean and consolidate the daily branch transaction files."""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
FILE_PATTERN = "transactions_*.csv"

# Columns every daily file must have (after known aliases are mapped).
REQUIRED_COLUMNS = {
    "transaction_id", "date", "branch", "customer_id", "product",
    "quantity", "unit_price", "payment_method", "payment_ref",
}
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
REJECTED_COLUMNS = [
    "source_file", "row_number", "reasons",
    "transaction_id", "date", "branch", "customer_id", "product",
    "quantity", "unit_price", "payment_method", "payment_ref",
]


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
    rejected = {col: _text(row.get(col)) for col in REJECTED_COLUMNS}
    rejected["source_file"] = row["_source_file"]
    rejected["row_number"] = row["_row_number"]
    rejected["reasons"] = "; ".join(reasons)
    return rejected


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
    stats: dict[str, Any] = {"files": [], "rows_read": 0}

    for path in files:
        file_stats = {"name": path.name, "read": 0, "accepted": 0, "rejected": 0, "notes": []}
        stats["files"].append(file_stats)

        try:
            header, rows = read_transactions(path)
        except (OSError, UnicodeDecodeError, csv.Error) as exc:
            file_stats["notes"].append(f"could not be read, skipped ({exc})")
            continue

        file_stats["read"] = len(rows)
        stats["rows_read"] += len(rows)
        if not header:
            file_stats["notes"].append("file is empty, nothing to process")
            continue
        check = validate_columns(header)

        for old, new in check["renamed"].items():
            file_stats["notes"].append(f"mapped column '{old}' to '{new}'")
        if check["extra"]:
            file_stats["notes"].append(f"ignored extra columns: {', '.join(check['extra'])}")

        if not check["ok"]:
            # Without a required column no row in this file can be trusted.
            reason = f"file is missing required columns: {', '.join(check['missing'])}"
            file_stats["notes"].append(f"whole file rejected, {reason}")
            for row in rows:
                rejected.append(make_rejected(row, [reason]))
            file_stats["rejected"] = len(rows)
            continue

        for row in rows:
            row = apply_aliases(row, check["renamed"])
            problems = find_problems(row, seen_ids)
            if problems:
                rejected.append(make_rejected(row, problems))
                file_stats["rejected"] += 1
            else:
                clean = clean_row(row)
                seen_ids[clean["transaction_id"]] = f"{path.name} row {row['_row_number']}"
                accepted.append(clean)
                file_stats["accepted"] += 1

    stats["accepted"] = len(accepted)
    stats["rejected"] = len(rejected)
    if stats["accepted"] + stats["rejected"] != stats["rows_read"]:
        raise RuntimeError("Row counts do not reconcile: accepted + rejected != rows read.")
    return accepted, rejected, stats


# Task 8: summary
def reason_category(reason: str) -> str:
    """Drop the row-specific detail from a reason, e.g. "quantity must be positive (-4)"."""
    return reason.split(" (")[0].split(":")[0]


def build_summary(stats: dict[str, Any]) -> str:
    """Build the plain-text processing summary."""
    accepted = stats["accepted_rows"]
    rejected = stats["rejected_rows"]

    reason_counts = Counter(
        reason_category(reason)
        for row in rejected
        for reason in row["reasons"].split("; ")
    )
    branch_totals: dict[str, int] = defaultdict(int)
    for row in accepted:
        branch_totals[row["branch"]] += row["line_total"]
    total_sales = sum(branch_totals.values())

    lines = [
        "SOKO FRESH - DAILY TRANSACTIONS PROCESSING SUMMARY",
        f"Run at: {stats['run_at']}",
        "",
        f"Files processed: {len(stats['files'])}",
    ]
    for f in stats["files"]:
        lines.append(f"  {f['name']}: read {f['read']}, accepted {f['accepted']}, rejected {f['rejected']}")
        lines.extend(f"    note: {note}" for note in f["notes"])
    for name in stats.get("skipped_files", []):
        lines.append(f"  {name}: skipped, no YYYY-MM-DD date in the file name")

    lines += [
        "",
        f"Rows read:     {stats['rows_read']}",
        f"Rows accepted: {stats['accepted']}",
        f"Rows rejected: {stats['rejected']}",
        f"Check: {stats['accepted']} + {stats['rejected']} = {stats['accepted'] + stats['rejected']}"
        f" ({'OK' if stats['accepted'] + stats['rejected'] == stats['rows_read'] else 'MISMATCH'})",
        "",
        "Rejection reasons (a row can have more than one):",
    ]
    if reason_counts:
        lines.extend(f"  {reason}: {count}" for reason, count in reason_counts.most_common())
    else:
        lines.append("  none")

    lines += ["", f"Accepted sales total: KES {total_sales:,}", "Sales by branch:"]
    lines.extend(f"  {branch}: KES {amount:,}" for branch, amount in sorted(branch_totals.items()))
    lines += ["", "Outputs:"]
    lines.extend(f"  {path}" for path in stats.get("outputs", []))
    return "\n".join(lines)


def _display_path(path: Path) -> str:
    """Show output paths relative to the project folder when possible."""
    try:
        return path.resolve().relative_to(BASE_DIR).as_posix()
    except ValueError:
        return str(path)


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

    consolidated_path = args.output / "consolidated_transactions.csv"
    rejected_path = args.output / "rejected_transactions.csv"
    summary_path = args.output / "summary.txt"

    try:
        accepted, rejected, stats = process_files(files)
        write_csv(consolidated_path, ACCEPTED_COLUMNS, accepted)
        write_csv(rejected_path, REJECTED_COLUMNS, rejected)

        stats["accepted_rows"] = accepted
        stats["rejected_rows"] = rejected
        stats["skipped_files"] = sorted(
            path.name for path in args.data.glob(FILE_PATTERN) if path not in files
        )
        stats["outputs"] = [_display_path(p) for p in (consolidated_path, rejected_path, summary_path)]
        stats["run_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        summary = build_summary(stats)
        summary_path.write_text(summary + "\n", encoding="utf-8")
    except (OSError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
