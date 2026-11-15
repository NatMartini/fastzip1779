import os
from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    environment: str = "development"
    database_url: str = "postgresql+psycopg://fastzip:local-db-only@localhost:5432/fastzip"
    db_host: str = "postgres"
    db_port: int = 5432
    db_user: str = "fastzip"
    db_name: str = "fastzip"
    db_password: str = ""
    session_secret: str = "local-development-only-change-before-deployment"
    secure_cookies: bool = False
    public_origin: str = "http://localhost:8080"
    s3_endpoint: str = "http://localhost:9000"
    s3_public_endpoint: str = "http://localhost:9000"
    s3_region: str = "us-east-1"
    s3_access_key: str = "fastzip-local"
    s3_secret_key: str = "local-storage-only-change-me"
    s3_bucket: str = "fastzip-media"
    backup_bucket: str = "fastzip-backups"
    backup_access_key: str = ""
    backup_secret_key: str = ""
    backup_age_recipient: str = ""
    do_token: str = ""
    do_droplet_ids: str = ""
    max_attempts: int = 3
    lease_seconds: int = 60
    heartbeat_seconds: int = 10
    compression_timeout: int = 300
    session_days: int = 7
    media_hours: int = 24
    max_active_jobs: int = 3
    auth_rate_limit: int = 10
    registration_rate_limit: int = 5
    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"
    pg_dump_bin: str = "pg_dump"
    pg_restore_bin: str = "pg_restore"
    age_bin: str = "age"

    @model_validator(mode="after")
    def load_secret_files(self):
        # Values stay out of service specifications; only *_FILE paths are in the environment.
        for name in (
            "db_password",
            "session_secret",
            "s3_access_key",
            "s3_secret_key",
            "backup_access_key",
            "backup_secret_key",
            "do_token",
        ):
            if filename := os.getenv(name.upper() + "_FILE"):
                setattr(self, name, Path(filename).read_text().strip())
        if self.db_password:
            self.database_url = URL.create(
                "postgresql+psycopg",
                username=self.db_user,
                password=self.db_password,
                host=self.db_host,
                port=self.db_port,
                database=self.db_name,
            ).render_as_string(hide_password=False)
        if self.environment == "production":
            if len(self.session_secret) < 32 or self.session_secret.startswith("local-"):
                raise ValueError(
                    "Production requires a random session secret of at least 32 characters"
                )
            if not self.secure_cookies or not self.public_origin.startswith("https://"):
                raise ValueError("Production requires HTTPS and secure cookies")
            if self.s3_secret_key.startswith("local-"):
                raise ValueError("Production requires private object storage credentials")
        if self.lease_seconds <= self.heartbeat_seconds * 2:
            raise ValueError("Lease must exceed twice the heartbeat interval")
        return self


@lru_cache
def settings() -> Settings:
    return Settings()
