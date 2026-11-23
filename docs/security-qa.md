# Security regression QA

These checks cover API security responsibilities: sessions, CSRF,
authorization, job ownership, upload failure recovery, administrator metadata
privacy, and telemetry correctness. The original four security suites exercise
existing behavior without application or deployment changes. The additional
boundary suites described below include two minimal input-error handling fixes;
dependencies and deployment configuration remain unchanged.

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
| [Upload failures](../tests/test_upload_failures.py) | `test_failed_upload_preserves_existing_data_and_allows_retry` | 4 | Storage/commit/cleanup failures return a safe 503, leave no failed Job, preserve existing data, and allow another upload after recovery |
| [Admin telemetry](../tests/test_admin_telemetry.py) | `test_empty_overview_has_safe_defaults` | 1 | Empty statistics return zero counts, null average/backup, and no workers |
| Admin telemetry | `test_empty_error_log_returns_empty_items` | 1 | An empty error log returns an empty list |
| Admin telemetry | `test_overview_24h_statistics` | 4 | Mixed, success-only, failure-only, and no-recent-completion cases produce correct statistics |
| Admin telemetry | `test_overview_24h_cutoff_is_inclusive` | 1 | Completions exactly at the 24-hour cutoff are included; older completions are excluded |
| Admin telemetry | `test_expired_jobs_are_not_counted_as_queued` | 1 | Expired jobs are grouped as EXPIRED and excluded from queue depth |
| Admin telemetry | `test_worker_online_boundary_and_heartbeat_fields` | 1 | Workers at or beyond the heartbeat threshold are offline; heartbeat fields and ordering are preserved |
| Admin telemetry | `test_latest_backup_is_selected_by_timestamp` | 2 | The newest successful or failed backup is returned by timestamp rather than insertion order |
| Admin telemetry | `test_error_filters_are_inclusive_and_ordered` | 8 | Service/time filters, inclusive boundaries, equivalent timezone offsets, and newest-first ordering work correctly |
| Admin telemetry | `test_error_pagination_has_no_duplicates_or_missing_rows` | 1 | Consecutive pages contain each expected event once and eventually return an empty page |
| Admin telemetry | `test_error_filters_reject_naive_or_invalid_timestamps` | 4 | Both time filters reject timezone-free and malformed timestamps with 422 |

The CSRF matrix covers register, login, logout, and upload against missing headers,
missing cookies, mismatched tokens, matching but forged tokens, cross-site requests,
and untrusted origins. Anonymous upload checks supply a valid CSRF token so that a
401 demonstrates the authentication check rather than a CSRF rejection.

Upload failure cases cover storage errors before and after object creation, a
database commit failure after a real uncommitted INSERT, and a commit failure
combined with object deletion failure. The last case intentionally retains a
Moto orphan; background orphan cleanup is outside this suite. Telemetry tests
freeze the API clock to check exact boundaries without sleeps.

## Interrupted media commits

The API and worker hold a PostgreSQL transaction lock for each object key from
the start of its upload through publication of its database reference. After
a disconnect, cleanup opens a fresh transaction and checks both file references
before deleting an object. If the writer still holds the lock or the database
is unavailable, the file stays in storage for a later orphan sweep.

The 12 regression cases cover lost acknowledgements, real rollbacks, unavailable
verification, a sweep overlapping either kind of upload, competing workers and
repeated finalization of the same attempt. Run them against the disposable
`TEST_DATABASE_URL` with `uv run --extra dev pytest -q tests/test_media_commit_recovery.py`.
[Regression tests](../tests/test_media_commit_recovery.py)

In a separate local run with PostgreSQL 17.12, MinIO and the HTTP API, we closed
the publishing connection immediately after PostgreSQL committed. The upload
returned HTTP 202 with one job. Its original signed download returned the exact
2,790 input bytes. The worker completed in one attempt despite a second lost
acknowledgement, and the signed result returned HTTP 200 with a decodable
940-byte WebP. Closing the connection before commit produced HTTP 503 on upload
and a successful retry for the worker. Both rolled-back objects were removed.
The full suite passed on the isolated PostgreSQL 17.12 server with 218 tests in
26.28 seconds, including the 12 commit recovery cases.

## Additional boundary suites

Five additional suites cover media/API admission limits, rate windows and
concurrent increments, CLI administrator provisioning, and password/hash edges.
Their **96 cases** brought the complete suite to **203 tests** at that milestone.
The original four focused security suites comprise an 87-case subset.

See [Boundary and Failure-Case Regression Tests](../README.md#boundary-and-failure-case-regression-tests)
for the per-file case counts, exact scenarios, commands, recorded local results,
and the two minimal input-error handling corrections exposed by the tests.

The filename suite submits multipart uploads, runs the compression worker, and
checks the saved name in job history. For both the original and the result, it
checks the API filename and the UTF-8 attachment name in the signed URL. Cases
cover directory removal, control characters, Unicode and punctuation, the
160-character limit, and the `upload` fallback. With a disposable
`TEST_DATABASE_URL` and FFmpeg/FFprobe available, run
`uv run --extra dev pytest -q tests/test_filename_handling.py`.
A local run using PostgreSQL 16.2 and Moto S3 passed all 10 filename cases and
all 232 tests in the full suite. [Filename regression tests](../tests/test_filename_handling.py)

In a separate local browser check against MinIO, the history entry and original
download both showed `旅行 + 100% #1.png`. The browser suggested
`旅行 + 100% #1.webp` when downloading the compressed image.

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

Run all four suites together; the current expected count is **87 passed** (30
authentication, 29 authorization, 4 upload failure, and 24 telemetry cases).
This is an expected count, not a recorded combined-run result:

```sh
TEST_DATABASE_URL='postgresql+psycopg://fastzip:test-only@127.0.0.1:55432/fastzip_security_test' \
  .venv/bin/python -m pytest \
    tests/test_auth_security.py tests/test_authorization.py \
    tests/test_upload_failures.py tests/test_admin_telemetry.py -v
```

To run a single suite, keep the same database assignment and pass only its test
file. To investigate one failure, append `-k test_name` using the coverage table.
Each file also contains focused run instructions in its opening docstring.

Check lint, formatting, and whitespace:

```sh
.venv/bin/ruff check \
  tests/test_auth_security.py tests/test_authorization.py \
  tests/test_upload_failures.py tests/test_admin_telemetry.py
.venv/bin/ruff format --check \
  tests/test_auth_security.py tests/test_authorization.py \
  tests/test_upload_failures.py tests/test_admin_telemetry.py
git diff --check
```

Stop the test container when finished:

```sh
docker stop fastzip-security-qa
```

No running API, worker, MinIO service, or FFmpeg installation is needed for these
four suites. They use TestClient, real PostgreSQL, Moto object storage, and in-process
Pillow image compression. Admin fixtures grant a role directly in the disposable
database; they do not exercise the server-side admin provisioning CLI.

## Remaining coverage gaps

The following scenarios have no dedicated tests or only partial coverage in the
current repository. These are test opportunities, not confirmed defects. The
table describes test source coverage, not measured line/branch coverage or proof
that a suite has passed.

| Area | Remaining scenarios |
| --- | --- |
| Admin provisioning CLI | Terminal EOF/interrupt handling and real interactive getpass behavior; boundary suites now exercise successful creation/login, invalid/duplicate email, password limits, and mismatched confirmation |
| Account/API input validation | Additional credential field-type combinations beyond the selected invalid/missing inputs |
| Rate limiting | Registration IP isolation across independent HTTP clients and sustained traffic across separate API processes; window boundaries and atomic concurrent database counts are now covered |
| Upload validation | Additional malformed metadata shapes and real near-limit video encoding under container resource limits; byte/pixel/duration/resolution admission boundaries and selected probe failures are now covered |
| Admin APIs | Multi-page admin job ordering; configured DigitalOcean metrics parsing and upstream failure handling |
| API failure handling | Readiness dependency failures, response security headers, and failure to persist an error event |

Remaining priorities include configured monitoring failures and deployment
fault injection. The deployment results are recorded in the
[validation record](validation.md).

## CI integration

The existing [Checks workflow](../.github/workflows/ci.yml) runs `uv run pytest -q`,
which discovers the existing security suites and the new boundary suites under
`tests/`. No workflow changes are needed. Checks
are triggered on pull requests and pushes to `main` or `develop`; pushing only to
`qa_dev` does not by itself trigger that workflow. Record the actual Actions run
for a commit before claiming CI success.
