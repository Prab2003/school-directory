# Source data and permissions

The MIT licence covers this project's own software, SQL schema and documentation.
It does **not** relicense the Department of Basic Education's spreadsheets,
school records, third-party database exports or government branding.

The DBE adapter discovers the most recent **listed** national quarterly file on
[EMIS Downloads](https://www.education.gov.za/Programmes/EMIS/EMISDownloads.aspx).
The period printed on that page is recorded as `dataset_release`; it is not a
claim that the source reflects every school change as of today.
[LURITS](https://www.lurits2.doe.gov.za/HomeLinks/Masterlist) is another official
masterlist portal, but this adapter does not scrape its interactive search.

Checked on 21 November 2026: EMIS Downloads lists Quarter 3 of 2025 as its newest
national release. The linked workbook has a National worksheet with `NatEmis`,
`Official_Institution_Name`, `Phase_PED`, `Province`, `Status`, `LMunName` and
`Town_City`. The adapter includes ordinary and special schools, validates national
coverage, and deliberately ignores principal names, contacts, enrolment counts,
coordinates and all other columns.

Phase mapping follows the existing Skolo Saka directory: primary and intermediate
schools map to `primary`, secondary to `high`, combined to `combined`, and supported
special-school phases to `other`. This is an application grouping, not a replacement
for the official education-phase terminology. `GT` and `GP` both map to Gauteng.
Stand-alone ECD centres are counted as excluded and existing records are retained.

DBE's [general terms](https://www.education.gov.za/terms.aspx) reserve copyright.
A dataset-specific unrestricted redistribution licence has not been established
by this project. Operators must establish the rights appropriate to their use,
including public APIs and bulk exports. No official spreadsheet or real-data
snapshot is included in the repository or its CI artifacts. Test data is fictional.

Schema setup, tests and local fictional examples work without access to DBE.
Importing a file does not make a school `verified` by the hosting organisation.
School-list provenance, operational status and local verification are separate.
