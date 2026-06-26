"""
Submission checklists for each compliance module.

Each checklist is a list of "steps". Each step has:
  key        – stable unique id (used to persist checkbox state; NEVER change once live)
  title      – step heading
  owner      – (optional) responsible party/department, shown as a chip
  note       – (optional) callout text shown as an "Important" banner inside the step
  items      – list of {key, text} sub-tasks (each is a checkbox)
  references – (optional) list of {label, url} links to authoritative guidance

Stable keys are critical: checkbox state is stored against module + item key,
so renaming a key resets that checkbox for everyone.
"""

CHECKLISTS = {
    # ════════════════════════════════════════════════════════════════
    # GBS — Global Business Services incentive claim (the dtic)
    # ════════════════════════════════════════════════════════════════
    "gbs": {
        "title": "GBS Claim Submission Checklist",
        "subtitle": "Global Business Services incentive claim — submitted to the dtic",
        "intro": (
            "This checklist defines the full workflow for processing and submitting a "
            "GBS incentive claim to the Department of Trade, Industry and Competition "
            "(the dtic). Work through each step in order and tick items off as they are "
            "completed. Progress is saved automatically."
        ),
        "deadline": "Within 30 days of the dtic claim-window notification",
        "steps": [
            {
                "key": "g1",
                "title": "Step 1 — Receive Notification from the dtic",
                "owner": "Compliance",
                "note": "You are usually given only 30 days to complete and submit everything.",
                "items": [
                    {"key": "g1a", "text": "Monitor email for communication from the dtic about the GBS claim window"},
                    {"key": "g1b", "text": "Confirm updated templates, guidelines and submission instructions have been received"},
                    {"key": "g1c", "text": "Record the submission deadline date"},
                ],
            },
            {
                "key": "g2",
                "title": "Step 2 — Download and Review All Updated Documents",
                "owner": "Compliance",
                "items": [
                    {"key": "g2a", "text": "Download only the updated claim forms and site visit templates provided"},
                    {"key": "g2b", "text": "Read the latest GBS guidelines (especially page 18 — offshore job eligibility)"},
                ],
            },
            {
                "key": "g3",
                "title": "Step 3 — Export and Prepare Employee Data",
                "owner": "Payroll / People",
                "items": [
                    {"key": "g3a", "text": "Request employee data from Payroll (export from PaySpace per GBS claim sheet headings)"},
                    {"key": "g3b", "text": "Confirm every employee is linked to a specific campaign (cannot claim otherwise)"},
                    {"key": "g3c", "text": "Remove any duplicate records"},
                    {"key": "g3d", "text": "Verify ID numbers are valid and South African"},
                    {"key": "g3e", "text": "Include only employees active from the first day of the claim quarter (1 Jan / 1 Apr / 1 Jul / 1 Oct) and prior"},
                    {"key": "g3f", "text": "Exclude non-qualifying staff (e.g. transport, support roles not billable to offshore clients)"},
                ],
            },
            {
                "key": "g4",
                "title": "Step 4 — Complete the GBS Claim Calculation Sheet",
                "owner": "Compliance",
                "items": [
                    {"key": "g4a", "text": "Fill in employee data according to the template"},
                    {"key": "g4b", "text": "Confirm correct campaign names"},
                    {"key": "g4c", "text": "Confirm start and end dates reflect active employment during the quarter"},
                ],
            },
            {
                "key": "g5",
                "title": "Step 5 — Gather All Required Supporting Documentation",
                "owner": "Finance",
                "note": "Request financial and tax documents from the Finance Department.",
                "items": [
                    {"key": "g5a", "text": "Export Revenue invoices (must match the amount reported in the Site Visit Report)"},
                    {"key": "g5b", "text": "Latest Audited Financial Statements"},
                    {"key": "g5c", "text": "PAYE Statements"},
                    {"key": "g5d", "text": "Lease agreements (for the operational premises)"},
                    {"key": "g5e", "text": "Utility bills (electricity, water for the office)"},
                    {"key": "g5f", "text": "Telecommunication bills (showing offshore client usage)"},
                    {"key": "g5g", "text": "Project Management Accounts"},
                    {"key": "g5h", "text": "Recruitment policies and staff employment summaries"},
                    {"key": "g5i", "text": "Contracts for offshore activities rendered"},
                    {"key": "g5j", "text": "Tax Clearance Certificate"},
                    {"key": "g5k", "text": "Cancelled cheque or bank confirmation letter"},
                ],
            },
            {
                "key": "g6",
                "title": "Step 6 — Complete the GBS Claim Form and Other Forms",
                "owner": "Compliance",
                "items": [
                    {"key": "g6a", "text": "GBS Claim Form"},
                    {"key": "g6b", "text": "Combined Site Visit Report (one or multiple sites, as applicable)"},
                    {"key": "g6c", "text": "Site Visit form per site (JHB & CPT)"},
                    {"key": "g6d", "text": "Consent Form for dtic Reporting and Publications"},
                    {"key": "g6e", "text": "Any additional documents requested by the dtic"},
                ],
            },
            {
                "key": "g7",
                "title": "Step 7 — Internal Quality Check",
                "owner": "Compliance",
                "items": [
                    {"key": "g7a", "text": "All forms are filled in correctly and consistently"},
                    {"key": "g7b", "text": "Export Revenue reported matches the invoice totals"},
                    {"key": "g7c", "text": "Employees match those eligible according to the guidelines"},
                    {"key": "g7d", "text": "No missing signatures (site visit reports, employee registers, etc.)"},
                ],
            },
            {
                "key": "g8",
                "title": "Step 8 — Submit to the Auditor",
                "owner": "External Auditor",
                "items": [
                    {"key": "g8a", "text": "Send the GBS Claim Calculation Sheet, Claim Form and supporting documents to the appointed external auditor"},
                    {"key": "g8b", "text": "Auditor reviews and validates the information (GBS Factual Findings)"},
                    {"key": "g8c", "text": "Auditor performs agreed-upon procedures (IDs, employment contracts, revenue proofs, etc.)"},
                    {"key": "g8d", "text": "Auditor prepares a Factual Findings Report"},
                    {"key": "g8e", "text": "Auditor signs off the claim documentation"},
                ],
            },
            {
                "key": "g9",
                "title": "Step 9 — Submit Final Package to the dtic",
                "owner": "Compliance",
                "note": "Email the confirmation and upload link to GBSClaims@thedtic.gov.za before the deadline.",
                "items": [
                    {"key": "g9a", "text": "Compile the completed and signed Claim Form"},
                    {"key": "g9b", "text": "Include the Auditor's Factual Findings Report"},
                    {"key": "g9c", "text": "Include all supporting schedules and documents"},
                    {"key": "g9d", "text": "Upload via OneDrive link or secure transfer platform, as instructed by the dtic"},
                    {"key": "g9e", "text": "Email confirmation and upload link to GBSClaims@thedtic.gov.za before the deadline"},
                ],
            },
            {
                "key": "g10",
                "title": "Step 10 — Prepare for Site Visit (if scheduled)",
                "owner": "Operations",
                "items": [
                    {"key": "g10a", "text": "Signed Site Visit Reports ready"},
                    {"key": "g10b", "text": "Signed Employee Verification Registers ready"},
                    {"key": "g10c", "text": "Premises ready for inspection"},
                    {"key": "g10d", "text": "Selected employee files easily accessible"},
                ],
            },
        ],
        "reminders": [
            "Use the updated templates — old forms will cause delays.",
            "No Job Displacement is allowed (you cannot replace local jobs).",
            "Offshore activity must be proven (e.g. through client contracts and invoices).",
            "Employees must work 40+ hours per week, excluding lunch breaks.",
            "Keep track of claim deadlines — late submissions may be rejected.",
        ],
    },

    # ════════════════════════════════════════════════════════════════
    # EE — Employment Equity (EEA2 & EEA4) — Dept of Employment & Labour
    # ════════════════════════════════════════════════════════════════
    "ee": {
        "title": "Employment Equity Compliance Checklist",
        "subtitle": "How to achieve EE compliance & submit EEA2 / EEA4 to the DoEL",
        "intro": (
            "This checklist walks a designated employer through the full Employment "
            "Equity cycle required by the Employment Equity Act 55 of 1998 (as amended) — "
            "from confirming your obligations, through workforce analysis and planning, "
            "to submitting the annual EEA2 and EEA4 reports to the Department of Employment "
            "and Labour. Each step links to the official source for more detail. "
            "Progress is saved automatically."
        ),
        "deadline": "Annual — manual by 1 Oct · online by 15 Jan",
        "steps": [
            {
                "key": "e1",
                "title": "Step 1 — Confirm you are a Designated Employer",
                "owner": "HR / Compliance",
                "note": (
                    "Since the Employment Equity Amendment Act came into effect (1 Jan 2025), "
                    "designation is based on employee count alone — the turnover threshold no longer applies."
                ),
                "items": [
                    {"key": "e1a", "text": "Confirm you employ 50 or more employees (designated employer)"},
                    {"key": "e1b", "text": "Identify the economic sector you fall under (for sectoral numerical targets)"},
                    {"key": "e1c", "text": "Register / confirm your details on the DoEL EE Online system"},
                ],
                "references": [
                    {"label": "Employment Equity Act 55 of 1998 (as amended)", "url": "https://www.labour.gov.za/Docer/Pages/Acts.aspx"},
                    {"label": "EE Amendment Act 4 of 2022", "url": "https://www.gov.za/documents/employment-equity-amendment-act"},
                ],
            },
            {
                "key": "e2",
                "title": "Step 2 — Assign responsibility for EE",
                "owner": "Executive",
                "items": [
                    {"key": "e2a", "text": "Appoint one or more senior managers responsible for EE (Section 24)"},
                    {"key": "e2b", "text": "Give them the authority, means and time to drive EE"},
                ],
                "references": [
                    {"label": "EE Act — Section 24 (Assignment of responsibility)", "url": "https://www.labour.gov.za/employment-equity"},
                ],
            },
            {
                "key": "e3",
                "title": "Step 3 — Communicate & consult employees",
                "owner": "HR / EE Committee",
                "items": [
                    {"key": "e3a", "text": "Establish an EE consultative committee representative of all groups (Sections 16–17)"},
                    {"key": "e3b", "text": "Display the EEA3 summary of the EE Act in the workplace (Section 25)"},
                    {"key": "e3c", "text": "Make the EE Act and process available/known to all employees"},
                ],
                "references": [
                    {"label": "EE Act — Sections 16, 17 & 25 (Consultation & disclosure)", "url": "https://www.labour.gov.za/employment-equity"},
                    {"label": "EEA3 — Summary of the Act (display poster)", "url": "https://www.labour.gov.za/Docer/Pages/Forms.aspx"},
                ],
            },
            {
                "key": "e4",
                "title": "Step 4 — Conduct a workforce & barriers analysis",
                "owner": "HR / EE Committee",
                "items": [
                    {"key": "e4a", "text": "Profile the workforce by race, gender & disability across all occupational levels"},
                    {"key": "e4b", "text": "Analyse policies, practices, procedures & the working environment for barriers (Section 19)"},
                    {"key": "e4c", "text": "Document the analysis using the EEA12 form"},
                ],
                "references": [
                    {"label": "EE Act — Section 19 (Analysis)", "url": "https://www.labour.gov.za/employment-equity"},
                    {"label": "EEA12 — Workforce analysis form", "url": "https://www.labour.gov.za/Docer/Pages/Forms.aspx"},
                ],
            },
            {
                "key": "e5",
                "title": "Step 5 — Develop the Employment Equity Plan",
                "owner": "HR / EE Committee",
                "items": [
                    {"key": "e5a", "text": "Set objectives, affirmative action measures, numerical goals & timeframes (Section 20)"},
                    {"key": "e5b", "text": "Align numerical targets with the Minister's sectoral targets"},
                    {"key": "e5c", "text": "Capture the plan on the EEA13 form (1–5 year duration)"},
                    {"key": "e5d", "text": "Consult the EE committee on the draft plan and finalise it"},
                ],
                "references": [
                    {"label": "EE Act — Section 20 (Employment Equity Plan)", "url": "https://www.labour.gov.za/employment-equity"},
                    {"label": "EEA13 — EE Plan form", "url": "https://www.labour.gov.za/Docer/Pages/Forms.aspx"},
                    {"label": "Sectoral numerical targets (EE Regulations)", "url": "https://www.gov.za/documents/employment-equity-act-proposed-sectoral-numerical-targets"},
                ],
            },
            {
                "key": "e6",
                "title": "Step 6 — Implement affirmative action measures",
                "owner": "Line Management / HR",
                "items": [
                    {"key": "e6a", "text": "Implement measures to eliminate identified barriers (Section 15)"},
                    {"key": "e6b", "text": "Promote diversity and equitable representation across occupational levels"},
                    {"key": "e6c", "text": "Provide reasonable accommodation for people with disabilities"},
                    {"key": "e6d", "text": "Retain, develop & train people from designated groups"},
                ],
                "references": [
                    {"label": "EE Act — Section 15 (Affirmative action measures)", "url": "https://www.labour.gov.za/employment-equity"},
                ],
            },
            {
                "key": "e7",
                "title": "Step 7 — Analyse remuneration & income differentials",
                "owner": "HR / Payroll / Finance",
                "items": [
                    {"key": "e7a", "text": "Analyse remuneration & benefits across occupational levels (Section 27)"},
                    {"key": "e7b", "text": "Identify disproportionate income differentials by race & gender"},
                    {"key": "e7c", "text": "Plan measures to progressively reduce unfair differentials"},
                    {"key": "e7d", "text": "Prepare the data needed for the EEA4 income differential report"},
                ],
                "references": [
                    {"label": "EE Act — Section 27 (Income differentials)", "url": "https://www.labour.gov.za/employment-equity"},
                    {"label": "EEA4 — Income differential statement", "url": "https://www.labour.gov.za/Docer/Pages/Forms.aspx"},
                ],
            },
            {
                "key": "e8",
                "title": "Step 8 — Monitor, review & keep records",
                "owner": "HR / EE Committee",
                "items": [
                    {"key": "e8a", "text": "Monitor implementation of the plan against numerical goals"},
                    {"key": "e8b", "text": "Review and adjust the EE plan as needed"},
                    {"key": "e8c", "text": "Keep EE records for the prescribed retention period"},
                ],
                "references": [
                    {"label": "EE Regulations 2014 (record-keeping)", "url": "https://www.labour.gov.za/Docer/Pages/Regulations.aspx"},
                ],
            },
            {
                "key": "e9",
                "title": "Step 9 — Submit EEA2 & EEA4 reports",
                "owner": "HR / Compliance",
                "note": (
                    "Manual submissions are due by the first working day of October; "
                    "electronic submissions via EE Online are due by 15 January."
                ),
                "items": [
                    {"key": "e9a", "text": "Complete the EEA2 report (workforce profile & numerical goals)"},
                    {"key": "e9b", "text": "Complete the EEA4 report (income differentials)"},
                    {"key": "e9c", "text": "Submit via the EE Online reporting portal before the deadline"},
                    {"key": "e9d", "text": "Save the submission confirmation / reference number"},
                ],
                "references": [
                    {"label": "EE Act — Section 21 (Reporting)", "url": "https://www.labour.gov.za/employment-equity"},
                    {"label": "EE Online reporting portal", "url": "https://ee.labour.gov.za"},
                    {"label": "EEA2 & EEA4 report forms", "url": "https://www.labour.gov.za/Docer/Pages/Forms.aspx"},
                ],
            },
            {
                "key": "e10",
                "title": "Step 10 — Display & retain the report",
                "owner": "HR",
                "items": [
                    {"key": "e10a", "text": "Display a summary of the most recent EEA2/EEA4 report on notice boards (Section 22)"},
                    {"key": "e10b", "text": "Retain copies of submitted reports and supporting records"},
                ],
                "references": [
                    {"label": "EE Act — Section 22 (Publication of report)", "url": "https://www.labour.gov.za/employment-equity"},
                ],
            },
        ],
        "reminders": [
            "Only designated employers (50+ employees) must report — but all employers must avoid unfair discrimination.",
            "Electronic submissions close on 15 January; manual submissions on the first working day of October.",
            "From 1 Jan 2025, an EE compliance certificate (Section 53) is required to do business with the State.",
            "Numerical targets must align with the Minister's sectoral targets for your sector.",
            "Non-compliance fines are significant — up to R1.5 million or a percentage of annual turnover.",
        ],
        "general_references": [
            {"label": "Department of Employment and Labour", "url": "https://www.labour.gov.za"},
            {"label": "EE Online reporting portal", "url": "https://ee.labour.gov.za"},
            {"label": "Commission for Employment Equity (CEE) annual reports", "url": "https://www.labour.gov.za/Documents/Annual%20Reports"},
        ],
    },
}


def get_checklist(module):
    """Return the checklist definition for a module, or None."""
    return CHECKLISTS.get(module)


def count_items(checklist):
    """Total number of tickable sub-items in a checklist."""
    if not checklist:
        return 0
    return sum(len(step["items"]) for step in checklist["steps"])
