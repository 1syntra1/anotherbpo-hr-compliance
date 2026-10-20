"""Company profile: append-only versions. The newest row is the current profile.

Generated reports store the id of the profile version they used, so later edits
never change what an old report says.
"""

FIELDS = [
    # (column, label, group)
    ("company_name", "Company name", "Identity"),
    ("trading_name", "Trading name", "Identity"),
    ("reg_number", "CIPC registration number", "Identity"),
    ("industry", "Industry", "Identity"),
    ("sector", "EE sector (for sectoral targets)", "Identity"),
    ("financial_year_end", "Financial year end (MM-DD)", "Identity"),
    ("paye_number", "PAYE reference", "Registrations"),
    ("uif_number", "UIF reference", "Registrations"),
    ("sdl_number", "SDL reference", "Registrations"),
    ("coida_number", "COIDA employer registration number", "Registrations"),
    ("seta", "SETA", "Registrations"),
    ("skills_levy", "Annual skills levy paid (R)", "Registrations"),
    ("designated_employer", "Designated employer (EE)", "Registrations"),
    ("physical_address", "Physical address", "Contact"),
    ("postal_address", "Postal address", "Contact"),
    ("contact_person", "Contact person", "Contact"),
    ("contact_email", "Contact email", "Contact"),
    ("contact_phone", "Contact phone", "Contact"),
    ("annual_turnover", "Annual turnover (R)", "Ownership & B-BBEE"),
    ("black_ownership_pct", "Black ownership (%)", "Ownership & B-BBEE"),
    ("black_women_ownership_pct", "Black women ownership (%)", "Ownership & B-BBEE"),
    ("ownership_notes", "Ownership notes", "Ownership & B-BBEE"),
]
COLUMNS = [f[0] for f in FIELDS]
NUMERIC = {"skills_levy", "annual_turnover", "black_ownership_pct", "black_women_ownership_pct"}
PERCENT = {"black_ownership_pct", "black_women_ownership_pct"}


class ProfileError(ValueError):
    pass


def current_profile(db):
    row = db.execute("SELECT * FROM company_profiles ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else {}


def profile_by_id(db, pid):
    if pid is None:
        return {}
    row = db.execute("SELECT * FROM company_profiles WHERE id=?", (pid,)).fetchone()
    return dict(row) if row else {}


def _clean(col, value):
    if value is None:
        return None
    if col in NUMERIC:
        s = str(value).strip().replace(" ", "").replace(",", "")
        if s == "":
            return None
        try:
            n = float(s)
        except ValueError:
            raise ProfileError("%s must be a number." % dict((f[0], f[1]) for f in FIELDS)[col])
        if n < 0 or n != n or n in (float("inf"),):
            raise ProfileError("%s cannot be negative." % dict((f[0], f[1]) for f in FIELDS)[col])
        if col in PERCENT and n > 100:
            raise ProfileError("Percentages cannot exceed 100.")
        return round(n, 2)
    s = str(value).strip()[:500]
    if col == "financial_year_end" and s:
        import re
        if not re.fullmatch(r"(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])", s):
            raise ProfileError("Financial year end must be MM-DD, e.g. 02-28.")
    if col == "designated_employer" and s and s not in ("Yes", "No"):
        raise ProfileError("Designated employer must be Yes or No.")
    return s or None


def save_profile(db, changes, user_id):
    """Merge `changes` into the current profile and append a new version if anything
    differs. Returns (profile_id, {col: (old, new)}). Does not commit."""
    cur = current_profile(db)
    new = {c: cur.get(c) for c in COLUMNS}
    for col, value in changes.items():
        if col in COLUMNS:
            new[col] = _clean(col, value)
    diff = {c: (cur.get(c), new[c]) for c in COLUMNS if cur.get(c) != new[c]}
    if not diff and cur:
        return cur["id"], {}
    cols = COLUMNS + ["created_by"]
    c = db.execute(
        "INSERT INTO company_profiles (%s) VALUES (%s)" % (",".join(cols), ",".join("?" * len(cols))),
        [new[k] for k in COLUMNS] + [user_id],
    )
    return c.lastrowid, diff
