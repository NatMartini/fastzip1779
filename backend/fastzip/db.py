import logging
import os
import socket
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from fastzip.config import settings
from fastzip.models import ErrorEvent

log = logging.getLogger("fastzip")


@lru_cache
def engine():
    return create_engine(settings().database_url, pool_pre_ping=True, pool_size=5, max_overflow=5)


def session_factory():
    return sessionmaker(engine(), expire_on_commit=False)


def database():
    with session_factory()() as db:
        yield db


def node_name():
    return os.getenv("NODE_NAME", socket.gethostname())


def record_error(service: str, message: str, job_id=None):
    # Only caller-provided safe messages are recorded; never raw storage/DB exceptions or URLs.
    log.error("service=%s node=%s job=%s message=%s", service, node_name(), job_id, message)
    try:
        with session_factory()() as db, db.begin():
            db.add(ErrorEvent(service=service, node=node_name(), job_id=job_id, message=message))
    except Exception:
        log.error("Error event could not be persisted")
