# Security regression QA

These checks cover API security responsibilities: sessions, CSRF,
authorization, job ownership, and administrator metadata privacy. They exercise
existing behavior without changing application code, dependencies, or deployment
configuration.

## Coverage and expected behavior

Test names below link to their source files. Counts include parameterized cases.

| Suite | Test | Cases | Expected behavior |
| --- | --- | ---: | --- |
| [Authentication](../tests/test_auth_security.py) | `test_expired_session_is_rejected` | 1 | An expired session returns 401 |
| Authentication | `test_login_rotates_session_and_rejects_old_token` | 1 | Login creates a new usable token; the old token returns 401 |
| Authentication | `test_failed_login_does_not_disclose_account_or_replace_session` | 1 | Wrong passwords and nonexistent accounts return the same 401 body; the current session remains usable |
| Authentication | `test_state_changing_routes_reject_invalid_csrf` | 24 | Four write routes reject six invalid request conditions with 403 |
| Authentication | `test_same_origin_request_with_valid_csrf_is_accepted` | 1 | A valid same-origin login succeeds |
| Authentication | `test_authentication_cookie_attributes` | 2 | Local and HTTPS cookie settings have the expected flags and lifetime; logout expires both cookies |
| [Authorization](../tests/test_authorization.py) | `test_admin_endpoint_permission_matrix` | 12 | Four admin endpoints return 401 for anonymous requests, 403 for ordinary users, and 200 for admins |
| Authorization | `test_anonymous_job_access_requires_login` | 5 | Anonymous job listing, detail, original/result download, and upload return 401 |
| Authorization | `test_other_accounts_cannot_access_job_or_discover_its_existence` | 6 | Other users and admins receive the same 404 body for another user's job and a nonexistent job |
| Authorization | `test_owner_can_access_completed_job_and_both_downloads` | 3 | The owner can view the completed job and obtain original/result download links |
| Authorization | `test_job_lists_and_pagination_only_include_own_jobs` | 2 | Ordinary users and admins see only their own jobs, totals, and active counts through `/api/jobs` |
| Authorization | `test_admin_job_listing_exposes_only_approved_metadata` | 1 | `/api/admin/jobs` exposes only the explicit metadata allowlist |

The CSRF matrix covers register, login, logout, and upload against missing headers,
missing cookies, mismatched tokens, matching but forged tokens, cross-site requests,
and untrusted origins. Anonymous upload checks supply a valid CSRF token so that a
401 demonstrates the authentication check rather than a CSRF rejection.

## Run locally

Run commands from the repository root in a macOS/Linux shell. Install Python 3.12+,
`uv`, and Docker, start Docker, then install the locked development dependencies:

```sh
uv sync --frozen --extra dev
```

Use only a disposable PostgreSQL database: [the fixtures](../tests/conftest.py)
apply migrations and truncate application tables between tests. Do not point
`TEST_DATABASE_URL` at a development database containing useful data or production.

For the first run, create the dedicated test container:

```sh
docker run -d --name fastzip-security-qa \
  -e POSTGRES_USER=fastzip \
  -e POSTGRES_PASSWORD=test-only \
  -e POSTGRES_DB=fastzip_security_test \
  -p 127.0.0.1:55432:5432 \
  postgres:17-bookworm
```

For subsequent runs, start that existing container instead:

```sh
docker start fastzip-security-qa
```

Wait until this check reports `accepting connections` before running pytest:

```sh
docker exec fastzip-security-qa pg_isready -U fastzip -d fastzip_security_test
```

Run both suites together; the current expected count is **59 passed** (30
authentication cases and 29 authorization cases):

```sh
TEST_DATABASE_URL='postgresql+psycopg://fastzip:test-only@127.0.0.1:55432/fastzip_security_test' \
  .venv/bin/python -m pytest tests/test_auth_security.py tests/test_authorization.py -v
```

To run a single suite, keep the same database assignment and pass only its test
file. To investigate one failure, append `-k test_name` using the coverage table.
Each file also contains focused run instructions in its opening docstring.

Check lint, formatting, and whitespace:

```sh
.venv/bin/ruff check tests/test_auth_security.py tests/test_authorization.py
.venv/bin/ruff format --check tests/test_auth_security.py tests/test_authorization.py
git diff --check
```

Stop the test container when finished:

```sh
docker stop fastzip-security-qa
```

No running API, worker, MinIO service, or FFmpeg installation is needed for these
two suites. They use TestClient, real PostgreSQL, Moto object storage, and in-process
Pillow image compression. Admin fixtures grant a role directly in the disposable
database; they do not exercise the server-side admin provisioning CLI.

## CI integration

The existing [Checks workflow](../.github/workflows/ci.yml) runs `uv run pytest -q`,
which discovers both files under `tests/`. No workflow changes are needed. Checks
are triggered on pull requests and pushes to `main` or `develop`; pushing only to
`qa_dev` does not by itself trigger that workflow. Record the actual Actions run
for a commit before claiming CI success.
