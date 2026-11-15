"""Initial schema, frozen independently of application models."""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE users (
      id UUID PRIMARY KEY, email VARCHAR(254) NOT NULL UNIQUE, password_hash TEXT NOT NULL,
      role VARCHAR(12) NOT NULL CHECK(role IN ('user','admin')), created_at TIMESTAMPTZ NOT NULL
    );
    CREATE TABLE sessions (
      token_hash VARCHAR(64) PRIMARY KEY, user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      expires_at TIMESTAMPTZ NOT NULL
    );
    CREATE INDEX ix_sessions_user_id ON sessions(user_id);
    CREATE INDEX ix_sessions_expires_at ON sessions(expires_at);
    CREATE TABLE jobs (
      id UUID PRIMARY KEY, user_id UUID NOT NULL REFERENCES users(id), filename VARCHAR(180) NOT NULL,
      media_type VARCHAR(12) NOT NULL CHECK(media_type IN ('image','video')),
      preset VARCHAR(20) NOT NULL CHECK(preset IN ('smaller','balanced','higher_quality')),
      status VARCHAR(16) NOT NULL CHECK(status IN ('QUEUED','PROCESSING','SUCCEEDED','FAILED','EXPIRED')),
      original_key VARCHAR(256) NOT NULL UNIQUE, output_key VARCHAR(256),
      original_size BIGINT NOT NULL CHECK(original_size > 0), output_size BIGINT, output_mime VARCHAR(64),
      attempts INTEGER NOT NULL CHECK(attempts >= 0), attempt_id UUID, worker_id VARCHAR(120),
      lease_until TIMESTAMPTZ, available_at TIMESTAMPTZ NOT NULL, created_at TIMESTAMPTZ NOT NULL,
      started_at TIMESTAMPTZ, completed_at TIMESTAMPTZ, expires_at TIMESTAMPTZ NOT NULL,
      media_deleted_at TIMESTAMPTZ, processing_seconds DOUBLE PRECISION, error TEXT
    );
    CREATE INDEX ix_jobs_user_id ON jobs(user_id);
    CREATE INDEX ix_jobs_status ON jobs(status);
    CREATE INDEX ix_jobs_expires_at ON jobs(expires_at);
    CREATE INDEX jobs_claim ON jobs(status, available_at, created_at);
    CREATE TABLE error_events (
      id BIGSERIAL PRIMARY KEY, service VARCHAR(32) NOT NULL, node VARCHAR(120) NOT NULL,
      job_id UUID, message TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL
    );
    CREATE INDEX ix_error_events_service ON error_events(service);
    CREATE INDEX ix_error_events_created_at ON error_events(created_at);
    CREATE TABLE worker_heartbeats (
      worker_id VARCHAR(120) PRIMARY KEY, node VARCHAR(120) NOT NULL, job_id UUID, seen_at TIMESTAMPTZ NOT NULL
    );
    CREATE TABLE rate_limits (
      key VARCHAR(64) NOT NULL, window_start BIGINT NOT NULL, count INTEGER NOT NULL,
      PRIMARY KEY(key, window_start)
    );
    CREATE TABLE backup_runs (
      id UUID PRIMARY KEY, status VARCHAR(16) NOT NULL, object_key VARCHAR(256), size_bytes BIGINT,
      duration_seconds DOUBLE PRECISION, created_at TIMESTAMPTZ NOT NULL, message TEXT
    );
    """)


def downgrade():
    for table in (
        "backup_runs",
        "rate_limits",
        "worker_heartbeats",
        "error_events",
        "jobs",
        "sessions",
        "users",
    ):
        op.drop_table(table)
