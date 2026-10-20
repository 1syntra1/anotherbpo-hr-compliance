"""Fill a NEW data directory with synthetic demo/test data (no real people).

    HR_DATA_DIR=/tmp/hrcs-demo python scripts/seed_demo.py

Refuses to run against a database that already has employees.
"""
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("HR_COOKIE_SECURE", "0")

import app as hr  # noqa: E402  (initialises / migrates the database)

FIRST = ["Thandi", "Sipho", "Lerato", "Johan", "Priya", "Ayesha", "Mandla", "Zoë", "Nomvula", "Pieter",
         "Kagiso", "Fatima", "Seán", "Lindiwe", "Ravi", "Anele", "Chantelle", "Bongani", "Marike", "Tumelo"]
LAST = ["Mokoena", "Naidoo", "van der Merwe", "Dlamini", "O'Brien", "Khumalo", "Pillay", "Botha",
        "Nkosi-Ndlovu", "Adams", "Mthembu", "Smith", "Govender", "Zulu", "Jacobs", "Petersen"]
LEVELS = ["Top Management", "Senior Management", "Professionally Qualified", "Skilled Technical",
          "Semi-Skilled", "Unskilled"]
RACES = ["African", "Coloured", "Indian", "White"]


def main(n=60):
    db = hr.connect_db()
    if db.execute("SELECT COUNT(*) FROM employees").fetchone()[0]:
        sys.exit("Refusing: this database already has employees.")
    rnd = random.Random(42)
    for i in range(n):
        foreign = i % 17 == 0
        start = "%d-%02d-01" % (rnd.randint(2012, 2025), rnd.randint(1, 12))
        term = "2025-%02d-15" % rnd.randint(1, 9) if i % 11 == 0 and start < "2025-01-01" else None
        db.execute(
            """INSERT INTO employees (emp_number, first_name, last_name, race, gender, disability, is_foreign_national,
                   occupational_level, employment_type, department, job_title, salary, employment_date,
                   termination_date, status, coida_class_code)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("AB%04d" % (i + 1), rnd.choice(FIRST), rnd.choice(LAST), None if foreign else rnd.choice(RACES),
             rnd.choice(["Male", "Female"]), "Yes" if i % 23 == 0 else "No", 1 if foreign else 0,
             LEVELS[min(5, int(rnd.random() ** 0.6 * 6))], "Temporary" if i % 9 == 0 else "Permanent",
             rnd.choice(["Operations", "Finance", "IT", "HR", "Sales"]), "Staff", round(rnd.uniform(7000, 95000), 2),
             start, term, "TERMINATED" if term else "ACTIVE", rnd.choice(["A", "A", "B"])))
    db.commit()
    print("Seeded %d synthetic employees into %s" % (n, hr.DATA_DIR))


if __name__ == "__main__":
    main()
