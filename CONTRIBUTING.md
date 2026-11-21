# Contributing

Fork, create a branch, make a focused change, and run:

```sh
python -m unittest discover -s tests -v
python -m py_compile school_directory.py
```

SQL changes also run the PostgreSQL integration tests in CI. Add tests for changed
behaviour and document source-format assumptions. Use fictional schools in tests.
Do not upload official spreadsheets, production records, private previews or credentials.
Report source corrections with a public official reference and EMIS number; do not
include pupils', parents', donors' or staff members' personal information.

Code contributions use the project's MIT licence. Source-data contributions need
their own documented reuse rights. Maintainers review changes before release;
contributions never write directly to the production directory.
