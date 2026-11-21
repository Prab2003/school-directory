# Public search contract

Portable handler: `school_search_api.mjs`. Supabase adapter:
`supabase/functions/school-directory-search/index.ts`.

Supabase route: `GET https://<project-ref>.supabase.co/functions/v1/school-directory-search?q=woodlands`.
A website can mount the handler at a route of its choosing.

Skolo Saka’s existing website adapter at
`https://www.skolosaka.co.za/api/public/schools/search?q=sunnyside` uses its own
`{ schools, total, limit }` response envelope (limit at most 10). The school-search
demo consumes that website adapter. The portable/Supabase adapter below uses
`{ items }` with a maximum of 6; consumers must choose the matching envelope.

```json
{"items":[{"id":"00000000-0000-4000-8000-000000000001","name":"Example Primary School","level":"primary","province":"Gauteng","municipality":"Example municipality","town":"Example town","verified":false}]}
```

This example is fictional. Return only these fields. Consumers should use the
school UUID for selection and show the location to distinguish duplicate names.
Database exports and public API redistribution both need appropriate source-data rights.

- Validate `q`: one value, minimum 2 characters, maximum 100; cap results at 6.
- Use the existing public-directory query/anonymous database permissions.
- Provide bounded timeouts, rate limiting, cache isolation and explicit error responses.
- CORS may allow a demo origin; it is not authentication or abuse prevention.
- On failure return an error with no source SQL or credentials. Never substitute fixtures.
- The importer is not callable by anonymous API clients.

Public dataset access does not grant access to donations, memberships, user contact
details or production administration. Hosting operators define their API quotas,
availability and data-use terms separately from the MIT software licence.
