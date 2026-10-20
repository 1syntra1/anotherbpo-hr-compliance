# Privacy (POPIA) controls and procedures

The register holds special personal information (race, disability) and other sensitive data
(ID numbers, salaries). Processing is for statutory compliance (EE Act, Skills Development Act,
COIDA, B-BBEE), which is the lawful basis. This document lists what the system does and what
the organisation must do.

## What the system enforces

| Area | Control |
|---|---|
| Access | Named accounts only; roles re-checked on every request; lockout; 15-minute idle sign-out; admin-only audit log, exports and permanent deletion. |
| Minimisation | EE report snapshots hold employee numbers and EE attributes only — no names or ID numbers. ID numbers are never shown in lists or reports. |
| Transmission | HTTPS via Coolify; Secure / HttpOnly / SameSite cookies; `Cache-Control: no-store` on signed-in pages. |
| Storage | Files outside the web root under random names; SHA-256 verified on download. Database and files on one volume — **use disk/volume encryption on the server**. |
| Accountability | Append-only audit log with user, IP, time and before/after values for employee, company, user, rule, workflow, report, import and export actions. |
| Logs | Access log excludes query strings; error pages never show data, SQL or paths. |
| Correction | Employees can be corrected at any time; the audit log keeps the previous values. |
| Access requests | Employees → (admin) **Export** gives a JSON of everything held about one person, including which stored reports contain them. |
| Retention / deletion | Terminate → archive keeps statutory history. Permanent deletion: admin only, archived only, refused while training/COIDA rows reference the person. Stored report snapshots are statutory records and are kept for the retention period even after deletion from the register. |

## What the organisation must do

1. Appoint / register the Information Officer.
2. Set retention periods per record type (e.g. EE records, payroll/COIDA records, training
   records) with HR and legal, and review archived employees annually for deletion.
3. Limit accounts to staff who need them; review users quarterly (Users page).
4. Keep backups encrypted and off-server; restrict who can read them.
5. Do not upload more personal information as "evidence" than the obligation requires.

## Breach response

1. **Contain:** disable affected accounts (Users → disable), rotate `HR_AUDIT_SECRET` (signs
   everyone out), change the admin password, block network access if needed.
2. **Assess:** export the audit log for the period (sign-ins, failed sign-ins, exports,
   downloads); identify whose data was involved.
3. **Notify:** the Information Regulator and affected data subjects as soon as reasonably
   possible (POPIA s22), with the information required.
4. **Recover:** restore from a verified backup if data was altered (DEPLOY_COOLIFY.md §7).
5. **Learn:** record the incident and the changes made.
