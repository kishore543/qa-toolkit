"""qa-toolkit: read a test case sheet - CSV or Excel (.xlsx) - into one standard shape.

Every team names its columns differently ("Expected result", "Expected Result",
"Expected outcome"...). This reader recognises the common names and returns each
test case with the same keys, so the other toolkit scripts never care which
format the sheet came in.

Excel sheets may have title rows or a summary table above the header, several tabs,
and a Summary tab - the header row (the first row with an ID column and an Expected
result column) is found automatically, and tabs without test cases are skipped.

Try it:
    python3 read_cases.py test-cases/login-test-cases.csv
    python3 read_cases.py "My Test Cases.xlsx"
"""
import csv
import os
import re
import sys

# Standard key -> column names that mean the same thing (lower-case, compared without punctuation).
COLUMNS = {
    "ID": ["id", "tc id", "test id", "test case id", "tc no", "tc number", "case id"],
    "Title": ["title", "test case", "test case title", "test case name", "test name", "scenario", "summary"],
    "Description": ["description"],
    "Requirement": ["requirement", "requirements", "req", "req id", "requirement id", "user story", "story"],
    "Area": ["area", "module", "feature", "area tab", "persona", "role"],
    "Type": ["type", "test type", "category", "kind"],
    "Priority": ["priority", "severity"],
    "Preconditions": ["preconditions", "precondition", "pre condition", "pre conditions", "prerequisites"],
    "Steps": ["steps", "test steps", "steps to reproduce", "procedure"],
    "Test data": ["test data", "data", "input data"],
    "Expected result": ["expected result", "expected results", "expected", "expected outcome", "expected behaviour", "expected behavior"],
    "Status": ["status"],
    "Manual result": ["result", "actual result", "outcome"],
}
REQUIRED = ("ID", "Expected result")   # a tab without these is not a test case tab


def simplify(name):
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(name or "").lower()).split())


def map_header(cells):
    """Return {column index: standard key} for one header row."""
    mapping = {}
    for index, cell in enumerate(cells):
        name = simplify(cell)
        for key, names in COLUMNS.items():
            if name in names and key not in mapping.values():
                mapping[index] = key
                break
    return mapping


def rows_to_cases(rows, sheet_name=""):
    """Find the header row in the first 60 rows, then turn the rows below it into test cases."""
    rows = list(rows)
    for header_at, cells in enumerate(rows[:60]):
        mapping = map_header(cells)
        if all(k in mapping.values() for k in REQUIRED):
            break
    else:
        return []

    cases = []
    for cells in rows[header_at + 1:]:
        if not any(str(c).strip() for c in cells if c is not None):
            if cases:
                break                      # first empty row after the table = end of the table
            continue
        case = {key: "" for key in COLUMNS}
        for index, key in mapping.items():
            if index < len(cells) and cells[index] is not None:
                case[key] = str(cells[index]).strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,19}", case["ID"]):
            continue                       # no ID, or a note / section title in the ID column
        if not case["Title"]:
            case["Title"] = case["Description"][:80]
        case["Status"] = "Blocked" + case["Status"][7:] if case["Status"].lower().startswith("blocked") else "Ready"
        case["Sheet"] = sheet_name
        cases.append(case)
    return cases


def read_cases(path):
    """Read a .csv or .xlsx test case sheet. Returns a list of test cases (dicts)."""
    if path.lower().endswith(".xlsx"):
        try:
            import openpyxl
        except ImportError:
            sys.exit("To read Excel files, install openpyxl once:  python3 -m pip install openpyxl")
        workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
        cases = []
        for sheet in workbook.worksheets:
            cases += rows_to_cases(sheet.iter_rows(values_only=True), sheet.title)
        return cases
    with open(path, encoding="utf-8-sig", newline="") as f:
        return rows_to_cases(csv.reader(f), os.path.basename(path))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python3 read_cases.py <test cases .csv or .xlsx>")
    found = read_cases(sys.argv[1])
    sheets = {}
    for c in found:
        sheets.setdefault(c["Sheet"], []).append(c)
    print("%d test cases found in %s" % (len(found), os.path.basename(sys.argv[1])))
    for name, items in sheets.items():
        blocked = sum(1 for c in items if c["Status"].startswith("Blocked"))
        print("  %-28s %3d test cases (%d blocked)   e.g. %s  %s"
              % (name[:28], len(items), blocked, items[0]["ID"], items[0]["Title"][:40]))
