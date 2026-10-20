"""Access to versioned regulatory rule sets (table `rule_sets`)."""
import json
from datetime import date


def _row(r):
    if r is None:
        return None
    d = dict(r)
    d["params"] = json.loads(d["params_json"])
    d["verified"] = bool(d.get("verified_at"))
    return d


def get_rule_set(db, code, on_date=None):
    """The version of `code` in force on `on_date` (default: today)."""
    on = (on_date or date.today().isoformat())[:10]
    r = db.execute(
        """SELECT * FROM rule_sets WHERE code=? AND effective_from <= ?
           AND (effective_to IS NULL OR effective_to >= ?)
           ORDER BY effective_from DESC, id DESC LIMIT 1""",
        (code, on, on)).fetchone()
    if r is None:  # nothing in force yet on that date: fall back to the earliest version
        r = db.execute("SELECT * FROM rule_sets WHERE code=? ORDER BY effective_from LIMIT 1",
                       (code,)).fetchone()
    return _row(r)


def rule_set_by_id(db, rid):
    if rid is None:
        return None
    return _row(db.execute("SELECT * FROM rule_sets WHERE id=?", (rid,)).fetchone())


def all_rule_sets(db):
    return [_row(r) for r in db.execute("SELECT * FROM rule_sets ORDER BY module, code, effective_from DESC")]


def unverified_count(db):
    return db.execute("SELECT COUNT(*) FROM rule_sets WHERE verified_at IS NULL AND superseded_by IS NULL"
                      ).fetchone()[0]
