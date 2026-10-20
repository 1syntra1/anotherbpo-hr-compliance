"""Initial regulatory rule sets, loaded once by migration 3.

EVERY rule set below is seeded UNVERIFIED (verified_at NULL). The values reflect the
developer's understanding of the regulation at the time of writing and must be
checked by a named person against the official source before any report produced
under it is relied on. The UI shows unverified rules prominently.

Never edit a rule set that has been used: add a new version with a new
effective_from date, so reports generated under the old version stay reproducible.
"""

LEVEL_THRESHOLDS_AMENDED = [  # (minimum points, level)
    [100, 1], [95, 2], [90, 3], [80, 4], [75, 5], [70, 6], [55, 7], [40, 8],
]
RECOGNITION_AMENDED = {"1": 135, "2": 125, "3": 110, "4": 100, "5": 80, "6": 60, "7": 50, "8": 10,
                       "NC": 0}

RULE_SETS = [
    {
        "module": "ee", "code": "EE-ACT-2025", "version": "2025.1",
        "regulator": "Department of Employment and Labour",
        "source_document": "Employment Equity Act 55 of 1998 as amended by the Employment Equity "
                           "Amendment Act 4 of 2022; EE Regulations; sectoral numerical targets "
                           "published 2025 (confirm Government Gazette reference)",
        "source_url": "https://www.labour.gov.za",
        "effective_from": "2025-01-01",
        "notes": "Designated-employer test as understood after the 2025 commencement: 50 or more "
                 "employees (turnover threshold removed). Reporting period 1 Oct - 30 Sep; online "
                 "submission due 15 January. VERIFY all of this, and the EEA2/EEA4 layouts, against "
                 "the current forms before filing.",
        "params": {
            "designated_min_employees": 50,
            "period_start_mmdd": "10-01",
            "period_end_mmdd": "09-30",
            "due_mmdd": "01-15",
            "occupational_levels": [
                "Top Management", "Senior Management",
                "Professionally Qualified", "Skilled Technical",
                "Semi-Skilled", "Unskilled",
            ],
            "race_groups": ["African", "Coloured", "Indian", "White"],
            "genders": ["Male", "Female"],
            "monthly_to_annual_factor": 12,
            "prorate_part_year": True,
        },
    },
    {
        "module": "bbbee", "code": "BBBEE-GENERIC-AMENDED", "version": "2013/2019",
        "regulator": "B-BBEE Commission / the dtic",
        "source_document": "Amended Codes of Good Practice (2013) as amended 2019 — Generic Scorecard",
        "source_url": "https://www.bbbeecommission.co.za",
        "effective_from": "2019-05-31",
        "notes": "Element weights, priority-element sub-minimums (40%) and one-level discounting as "
                 "understood by the developer. Sector codes override these. VERIFY.",
        "params": {
            "scorecard": "GENERIC",
            "elements": [
                {"key": "ownership", "label": "Ownership", "max": 25, "bonus": 0, "priority": True},
                {"key": "management", "label": "Management Control", "max": 19, "bonus": 0, "priority": False},
                {"key": "skills", "label": "Skills Development", "max": 20, "bonus": 5, "priority": True},
                {"key": "esd", "label": "Enterprise & Supplier Development", "max": 40, "bonus": 4, "priority": True},
                {"key": "sed", "label": "Socio-Economic Development", "max": 5, "bonus": 0, "priority": False},
            ],
            "subminimums": [
                {"key": "ownership_net_value", "label": "Ownership: net value", "element": "ownership",
                 "max": 8, "min_pct": 40},
                {"key": "skills_total", "label": "Skills Development (excl. bonus)", "element": "skills",
                 "max": 20, "min_pct": 40},
                {"key": "esd_preferential_procurement", "label": "ESD: preferential procurement (excl. bonus)",
                 "element": "esd", "max": 25, "min_pct": 40},
                {"key": "esd_supplier_development", "label": "ESD: supplier development", "element": "esd",
                 "max": 10, "min_pct": 40},
                {"key": "esd_enterprise_development", "label": "ESD: enterprise development", "element": "esd",
                 "max": 5, "min_pct": 40},
            ],
            "priority_rule": "all",
            "discount_levels": 1,
            "level_thresholds": LEVEL_THRESHOLDS_AMENDED,
            "recognition_pct": RECOGNITION_AMENDED,
            "turnover_max_eme": 10000000,
            "turnover_max_qse": 50000000,
        },
    },
    {
        "module": "bbbee", "code": "BBBEE-QSE-AMENDED", "version": "2013/2019",
        "regulator": "B-BBEE Commission / the dtic",
        "source_document": "Amended Codes of Good Practice (2013) as amended 2019 — QSE Scorecard",
        "source_url": "https://www.bbbeecommission.co.za",
        "effective_from": "2019-05-31",
        "notes": "QSEs must meet Ownership plus at least one of Skills Development or ESD (as "
                 "understood). 51% black-owned QSE: Level 2; 100%: Level 1 (sworn affidavit). VERIFY.",
        "params": {
            "scorecard": "QSE",
            "elements": [
                {"key": "ownership", "label": "Ownership", "max": 25, "bonus": 0, "priority": True},
                {"key": "management", "label": "Management Control", "max": 15, "bonus": 0, "priority": False},
                {"key": "skills", "label": "Skills Development", "max": 25, "bonus": 5, "priority": True},
                {"key": "esd", "label": "Enterprise & Supplier Development", "max": 30, "bonus": 4, "priority": True},
                {"key": "sed", "label": "Socio-Economic Development", "max": 5, "bonus": 0, "priority": False},
            ],
            "subminimums": [
                {"key": "ownership_net_value", "label": "Ownership: net value", "element": "ownership",
                 "max": 8, "min_pct": 40},
                {"key": "skills_total", "label": "Skills Development (excl. bonus)", "element": "skills",
                 "max": 25, "min_pct": 40},
                {"key": "esd_preferential_procurement", "label": "ESD: preferential procurement (excl. bonus)",
                 "element": "esd", "max": 15, "min_pct": 40},
                {"key": "esd_supplier_development", "label": "ESD: supplier development", "element": "esd",
                 "max": 10, "min_pct": 40},
                {"key": "esd_enterprise_development", "label": "ESD: enterprise development", "element": "esd",
                 "max": 5, "min_pct": 40},
            ],
            "priority_rule": "ownership_plus_one",
            "discount_levels": 1,
            "level_thresholds": LEVEL_THRESHOLDS_AMENDED,
            "recognition_pct": RECOGNITION_AMENDED,
            "auto_level_black_owned": [[100, 1], [51, 2]],
            "turnover_max_eme": 10000000,
            "turnover_max_qse": 50000000,
        },
    },
    {
        "module": "bbbee", "code": "BBBEE-EME-AMENDED", "version": "2013/2019",
        "regulator": "B-BBEE Commission / the dtic",
        "source_document": "Amended Codes of Good Practice (2013) as amended 2019 — EME",
        "source_url": "https://www.bbbeecommission.co.za",
        "effective_from": "2019-05-31",
        "notes": "EME (turnover up to R10m): 100% black-owned Level 1, at least 51% Level 2, "
                 "otherwise Level 4 (as understood). VERIFY.",
        "params": {
            "scorecard": "EME",
            "auto_level_black_owned": [[100, 1], [51, 2], [0, 4]],
            "recognition_pct": RECOGNITION_AMENDED,
            "turnover_max_eme": 10000000,
        },
    },
    {
        "module": "coida", "code": "COIDA-ROE", "version": "2024",
        "regulator": "Compensation Fund",
        "source_document": "COIDA 130 of 1993; annual notice of maximum earnings (confirm gazette)",
        "source_url": "https://www.labour.gov.za/compensation-fund",
        "effective_from": "2024-03-01",
        "notes": "Assessment = earnings (capped per employee at the annual maximum) / 100 x the "
                 "employer's assessment rate. Rates are NOT defaulted: enter each class rate from "
                 "the employer's own assessment notice. The maximum below is the figure previously "
                 "hard-coded in the app; VERIFY it for each assessment year.",
        "params": {
            "max_earnings_by_year": {"2024": 563520.00},
            "rate_basis": "per R100 of earnings",
        },
    },
    {
        "module": "deadlines", "code": "STATUTORY-DEADLINES", "version": "2026.1",
        "regulator": "Various",
        "source_document": "Default due dates used when a new reporting period is opened",
        "source_url": "",
        "effective_from": "2025-01-01",
        "notes": "Defaults only; every period's due date is editable. VERIFY each against the "
                 "regulator's current notice (WSP/ATR SETA window, COIDA ROE season, CIPC BO).",
        "params": {
            "ee": {"rule": "fixed", "mmdd": "01-15", "year_offset": 1},
            "wsp": {"rule": "fixed", "mmdd": "04-30", "year_offset": 0},
            "coida": {"rule": "fixed", "mmdd": "05-31", "year_offset": 1},
            "cipc_bo": {"rule": "manual",
                        "note": "Filed with the CIPC annual return; date depends on incorporation date"},
            "gbs": {"rule": "manual", "note": "Within 30 days of dtic notification"},
            "bbbee": {"rule": "manual", "note": "Before the current certificate/affidavit expires"},
        },
    },
]
