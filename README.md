# Day 2: Multi-File Data Processing Pipeline

Soko Fresh branches drop a CSV of each day's sales into a shared folder. `pipeline.py` finds every daily file, checks its columns and rows, cleans the rows it can trust, combines them into one CSV and writes every refused row to a separate CSV with the reasons.

## Project structure

```text
day2-multi-file-pipeline/
|-- pipeline.py
|-- data/
|   |-- transactions_2026-10-01.csv
|   |-- transactions_2026-10-02.csv
|   `-- transactions_2026-10-03.csv
|-- output/
|   |-- consolidated_transactions.csv
|   |-- rejected_transactions.csv
|   `-- summary.txt
|-- requirements.txt
`-- README.md
```

## How to run

Only the Python standard library is used (Python 3.10+), so there is nothing to install.

```powershell
cd E:\Data\day2-multi-file-pipeline
python pipeline.py
```

The summary is printed and the three files in `output/` are written (overwritten on every run).

Optional arguments:

| Argument | Default | Purpose |
|---|---|---|
| `--data` | `data/` | Folder containing the `transactions_*.csv` files |
| `--output` | `output/` | Folder for the three output files |

To add a new day, drop `transactions_YYYY-MM-DD.csv` into `data/` and run again. No code changes are needed.

## Approach

| Task | Function | What it does |
|---|---|---|
| 1 | `read_transactions(path)` | Reads one file with `csv.DictReader`. Returns the header and a list of row dicts. Each row stores `_source_file` and `_row_number` (line in the file, header = line 1). |
| 2 | `discover_files(folder, pattern)` | `Path.glob("transactions_*.csv")`, then sorts by the YYYY-MM-DD date read from each file name (`file_date`). Files without a valid date in the name, e.g. `transactions_backup.csv`, are skipped with a warning. |
| 3 | `validate_columns(header, required)` | Maps known aliases, then uses set difference to find missing (`required - columns`) and extra (`columns - required`) columns. |
| 4 | `find_problems(row, seen_ids)` | Collects every problem with a row: blank required values, quantity or unit price that is not a positive whole number, bad date format, and a transaction_id already accepted earlier in the run. |
| 5 | `clean_row(row)` | Strips whitespace (dict comprehension), keeps only the output columns (a set), converts `quantity` and `unit_price` to int and adds `line_total = quantity * unit_price`. No pandas. |
| 6 | `write_csv(...)` | Writes accepted rows to `output/consolidated_transactions.csv`. |
| 7 | `write_csv(...)` | Writes rejected rows to `output/rejected_transactions.csv`. |
| 8 | `build_summary(stats)` | Builds the summary in the layout from `expected_output.md`, prints it and saves it to `output/summary.txt`. |

`process_files` ties the steps together. At the end it checks that accepted + rejected equals rows read and stops with an error if not, so no row can disappear silently.

## Output files

**consolidated_transactions.csv**

```text
transaction_id,date,branch,customer_id,product,quantity,unit_price,line_total,payment_method,payment_ref,source_file
```

**rejected_transactions.csv**

```text
source_file,row_number,transaction_id,reasons,date,branch,customer_id,product,quantity,unit_price,payment_method,payment_ref,cashier
```

`reasons` lists every problem for that row, separated by `; `. After the four lead columns come the row's original columns, written as they were in the source file (only trimmed), so finance can see exactly what the branch sent. Extra columns from any file (here `cashier` from the Oct 3 file) are added at the end and left blank for files that don't have them. `qty` values appear under `quantity`.

**summary.txt** (also printed at the end of the run):

```text
=== Processing summary ===
Files discovered:        3
Files processed:         3
Files with column issues:1  (transactions_2026-10-03.csv: missing quantity (mapped from qty), extra cashier)
Rows read:               43
Rows accepted:           34
Rows rejected:           9
  - missing values:      4
  - invalid numbers:     2
  - duplicate IDs:       3
Total revenue (KES):     22,775
Run finished at:         2026-10-27T22:48:41
```

- **Files processed** counts files whose rows were checked. A file that is empty, unreadable or rejected as a whole is discovered but not processed.
- The breakdown counts rows. A row with problems in two groups (for example a missing branch and an invalid quantity) is counted in both, so the groups can add up to more than Rows rejected. An `- other:` line is added only when a row fails for another reason (bad date, too many fields, whole file rejected).
- **Total revenue** is the sum of `line_total` in the consolidated CSV, so the summary reconciles with both CSVs: 34 + 9 = 43 rows read.

## Results for the three provided files

| File | Read | Accepted | Rejected |
|---|---|---|---|
| transactions_2026-10-01.csv | 15 | 15 | 0 |
| transactions_2026-10-02.csv | 16 | 9 | 7 |
| transactions_2026-10-03.csv | 12 | 10 | 2 |
| **Total** | **43** | **34** | **9** |

Rejected rows:

| File, row | Transaction | Reason |
|---|---|---|
| 10-02, row 4 | TXN-1018 | missing customer_id |
| 10-02, row 5 | TXN-1019 | missing unit_price |
| 10-02, row 6 | TXN-1012 | duplicate, already accepted from 10-01 row 13 (re-sent by a retry) |
| 10-02, row 8 | TXN-1021 | missing quantity |
| 10-02, row 11 | TXN-1023 | duplicate, already accepted from 10-02 row 10 |
| 10-02, row 13 | TXN-1025 | missing branch |
| 10-02, row 16 | TXN-1028 | quantity is not a whole number ('two') |
| 10-03, row 7 | TXN-1029 | duplicate, already accepted from 10-02 row 17 |
| 10-03, row 8 | TXN-1035 | quantity must be positive (-4) |

## Assumptions and decisions

### The file with the unexpected header (transactions_2026-10-03.csv)

After the POS upgrade this file uses `qty` instead of `quantity` and adds a `cashier` column.

- **`qty` is mapped to `quantity`** instead of rejecting the file. The values in the column are the same kind of data (whole-number quantities), only the name changed. Rejecting the file would throw away a full day of valid sales for every branch because of a rename. The mapping lives in one place, `COLUMN_ALIASES`, so only renames we know about are accepted. Each mapping is reported in the summary so it is visible, not silent.
- **`cashier` is left out of the consolidated file.** The other days don't have it and it isn't in the expected output. It is kept in the rejected file so rejected Oct 3 rows still show everything the branch sent, and it is reported under column issues in the summary.
- **A file still missing a required column after alias mapping is rejected as a whole.** Every row from it goes to the rejected CSV with the reason `file is missing required columns: ...`, so the row counts still reconcile. I tested this with a fourth file without a `customer_id` column.

### Duplicates: which copy is kept

- **The first accepted copy is kept**: the one in the earliest file, and the earliest row within a file. Later copies with the same `transaction_id` go to the rejected file, and the reason names the file and row of the kept copy.
- For the provided data all three repeats (TXN-1012, TXN-1023, TXN-1029) are exact copies of the kept row, so each transaction appears once in the combined file. TXN-1029 in the Oct 3 file only differs by the new `cashier` column.
- Only *accepted* IDs count as seen. If the first copy of an ID had been invalid (for example a blank price) and a later copy was valid, the valid copy would be accepted, so a real sale isn't lost.

### Values

- Required values: every column except `payment_ref`. `payment_ref` is blank for cash sales in all files, so a blank value there is allowed.
- `quantity` and `unit_price` must be whole numbers greater than zero. Prices in the files are whole shillings, so they are stored as int.
- `date` must be YYYY-MM-DD. The row's own date is kept even if it differs from the file date (TXN-1012 in the Oct 2 file is dated Oct 1 because it was re-sent).
- Leading and trailing spaces are removed from every value.

## Error handling

| Situation | Result |
|---|---|
| Data folder does not exist | `ERROR: data folder not found: ...`, exit code 1 |
| No `transactions_*.csv` files | `ERROR: no transactions_*.csv files found in ...`, exit code 1 |
| A file cannot be read (permissions, bad encoding) | Listed under column issues in the summary and not processed; the others are still processed |
| A file is completely empty | Listed under column issues as `empty file, no header`; the others are still processed |
| A matching file has no date in its name | Skipped with `WARNING: skipped ..., no YYYY-MM-DD date in the file name` |
| Extra spaces in the header or values | Trimmed before checking |
| A file is missing a required column | Whole file rejected, every row in the rejected CSV with the reason |
| A row has more fields than the header | Rejected with `row has more fields than the header` |
| Output folder cannot be written | `ERROR: ...`, exit code 1 |
| Counts do not reconcile | `ERROR: Row counts do not reconcile ...`, exit code 1 |

These cases were tested by running with `--data` pointing at an empty folder, a folder that doesn't exist, and a copy of `data/` with extra files added: `transactions_2026-10-04.csv` with no `customer_id` column, an empty `transactions_2026-10-05.csv`, `transactions_2026-09-30.csv` with spaces around the header and values (it was correctly sorted first), and `transactions_backup.csv`.
