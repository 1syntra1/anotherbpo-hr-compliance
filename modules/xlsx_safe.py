"""Protection against spreadsheet formula / CSV injection.

Generated reports contain text typed by users (names, providers, notes). A value
such as  =HYPERLINK("http://evil","click")  would otherwise become a live formula
when the report is opened. Reports written by this app contain no formulas at all
(totals are calculated in Python and written as values), so every formula-typed
cell is converted back to plain text before saving.
"""
DANGEROUS_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def neutralise_workbook(wb):
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if cell.data_type == "f":
                    cell.data_type = "s"


def safe_csv_value(value):
    """For CSV exports: prefix risky leading characters so spreadsheet apps show text."""
    if isinstance(value, str) and value.startswith(DANGEROUS_PREFIXES):
        return "'" + value
    return value
