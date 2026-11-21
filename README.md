# School directory

Reusable school-directory SQL, a validated DBE importer, import previews and an
audit trail. Built by [Skolo Saka](https://www.skolosaka.co.za). The separate
[`school-search`](https://github.com/skolo-saka/school-search) React component
can use this directory through a `searchSchools` callback.

**Software: MIT. Source datasets: separate terms.** See [DATA_SOURCES.md](DATA_SOURCES.md).
This repository publishes code and the database structure, not production records
or credentials. No paid service or npm/Python dependency is required.

## Run locally

Python 3.11+ is enough to validate CSV/XLSX files and run the tests:

```sh
python -m unittest discover -s tests -v
python school_directory.py --help
python school_directory.py
```

The last command discovers and downloads the most recent national file listed by
DBE, validates it, and prints **counts and provenance only**. It does not contact
your database. Source layouts, duplicate EMIS numbers, invalid fields, partial
coverage, unknown phases, macros and formulas fail closed.

## Database setup

Requires PostgreSQL 15+, its `pg_trgm` extension and `psql`. Apply
`sql/001_school_directory.sql` followed by `sql/002_school_search.sql` as your
database owner through your normal migration process. On an empty database it
creates a minimal `public.schools` table; on Skolo Saka it reuses the existing
table and enum without replacing them. An empty database requires an explicit
reviewed bootstrap; automatic jobs will not populate it blindly.

Private tables in `school_directory` contain source versions, import runs,
staging records, last-seen source fields and before/after changes. They are not
exposed through the public Data API. Import functions use invoker security;
only the owner and Supabase `service_role` receive access. Public schools have
read-only access through RLS; this module grants no public write access.

Set `DATABASE_URL` privately in the environment. Then:

```sh
python school_directory.py --database       # preview; school rows unchanged
python school_directory.py --apply          # preview, check thresholds, atomic apply
```

For your own empty database, inspect `--database --bootstrap` first, then use
`--apply --bootstrap` for that reviewed initial import. Bootstrap cannot update a
non-empty directory and is never used by the normal scheduled job.

The connection string is passed to `psql` through environment fields, never as a
process argument or a log line. Remote connections require TLS. Do not commit
environment files, staging SQL, spreadsheets or database dumps.

To use an already downloaded official file:

```sh
python school_directory.py --file private/national.xlsx --release 2025-Q3 --source-url 'https://www.education.gov.za/LinkClick.aspx?...' --database
```

## What an update can do

- Match an existing school by its unique EMIS number; its UUID and relationships stay intact.
- Insert new open schools with new IDs and `verified=false`.
- Update source-owned name, phase and location fields on unverified schools.
- Preserve non-empty local locations when the source has missing/unknown values.
- Preserve verification flags and local edits detected against the last applied snapshot.
  On the first import, existing differences are protected until ownership is established.
- Record missing schools, closures and unmatched EMIS successor conflicts for review;
  never delete schools or remap an existing canonical EMIS because of a historical alias.
- Reject older source releases, incomplete national coverage, more than 10% new/changed
  records or more than 5% missing records. Floors of 100 changes and 25 missing apply.
- Recheck the preview fingerprint under a database lock before applying; all live
  writes, observations and audit records commit together or roll back together.
- Skip reapplying an unchanged, previously applied source checksum and period.

A run requiring review exits with status 2 and retains its private staging data.
A failed validation exits 1. Successful preview/application/unchanged checks exit 0.
Review is a real operational outcome: inspect and resolve it, rather than disabling
validation or labelling the current database "up to date".

## Schedule and monitoring

Use the [GitLab example](examples/gitlab-school-directory.yml) in a **private**
deployment repository. Set the protected `DATABASE_URL` secret for the intended
environment, and create a daily schedule. Never expose deployment credentials to
public pull requests. A scheduled job downloads new sources but writes only when
validation and change limits pass. Start with UAT and an inspected preview.

Record `dataset_release`, `source_sha256`, `last_checked_at` and
`last_successful_sync_at` separately. A successful check is not a newer dataset.
Configure the runner's existing failure notifications for exit 1/2. Source download
failures are visible as failed jobs; the last successful database timestamp stays intact.

The core importer does not send emails, run a public database, or promise an API SLA.
Source files and private previews should not be CI artifacts. Completed staging rows
are deleted after application. Retain or clear old incomplete/review runs through
your operator process, with an appropriate retention period.

## API integration

See [API.md](API.md) for the public response contract and permissions. A website
adapter should reuse the same school lookup as its own search, return an explicit
field allowlist, and supply the React component's `searchSchools` callback.

`school_search_api.mjs` is a dependency-free Request/Response handler. The
`supabase/functions/school-directory-search/index.ts` adapter uses the anonymous
database role and the public search RPC. Deploy it to your own project with
anonymous function invocation enabled for this public read-only endpoint. The
browser does not receive database administrator credentials. Its in-memory
rate limit is per runtime instance, not a global quota or availability guarantee.

Contributions: [CONTRIBUTING.md](CONTRIBUTING.md). Security: [SECURITY.md](SECURITY.md).
