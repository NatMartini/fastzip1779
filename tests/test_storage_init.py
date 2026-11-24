import runpy
import sys
from pathlib import Path
from unittest.mock import Mock, call

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from botocore.stub import Stubber
from fastzip.cli import main
from fastzip.config import settings
from fastzip.storage import Storage

COMPOSE_INIT = Path(__file__).resolve().parents[1] / "scripts/init_storage.py"


@pytest.mark.parametrize("entry", ["cli", "compose"])
@pytest.mark.parametrize("region", ["us-east-1", "eu-west-1"])
def test_initializers_create_media_and_backup_in_configured_region(monkeypatch, entry, region):
    config = settings()
    monkeypatch.setattr(config, "s3_region", region)
    monkeypatch.setattr(config, "s3_endpoint", f"https://s3.{region}.amazonaws.com")
    monkeypatch.setattr(config, "s3_bucket", "init-media")
    monkeypatch.setattr(config, "backup_bucket", "init-backups")
    monkeypatch.setattr(sys, "argv", ["fastzip", "init-storage"])
    if entry == "cli":
        main()
    else:
        runpy.run_path(str(COMPOSE_INIT))

    for store in (Storage(), Storage(backup=True)):
        location = store.client.get_bucket_location(Bucket=store.bucket)["LocationConstraint"]
        assert (location or "us-east-1") == region


def test_existing_bucket_only_needs_a_head_request(s3):
    with Stubber(s3.client) as stub:
        stub.add_response("head_bucket", {}, {"Bucket": s3.bucket})
        s3.ensure_bucket()
        stub.assert_no_pending_responses()


def test_bucket_access_denied_is_propagated(s3):
    with Stubber(s3.client) as stub:
        stub.add_client_error("head_bucket", "AccessDenied", http_status_code=403)
        with pytest.raises(ClientError, match="AccessDenied"):
            s3.ensure_bucket()


@pytest.mark.parametrize("conflict", ["BucketAlreadyOwnedByYou", "BucketAlreadyExists"])
def test_concurrent_creation_checks_ownership(s3, conflict):
    with Stubber(s3.client) as stub:
        stub.add_client_error("head_bucket", "404", http_status_code=404)
        stub.add_client_error("create_bucket", conflict, http_status_code=409)
        if conflict == "BucketAlreadyOwnedByYou":
            s3.ensure_bucket()
        else:
            with pytest.raises(ClientError, match=conflict):
                s3.ensure_bucket()
        stub.assert_no_pending_responses()


@pytest.mark.parametrize("becomes_ready", [True, False])
def test_compose_waits_for_storage_with_bounded_retries(monkeypatch, becomes_ready):
    unavailable = EndpointConnectionError(endpoint_url="http://minio:9000")
    ensure = Mock(side_effect=[unavailable, None] if becomes_ready else unavailable)
    sleep = Mock()
    monkeypatch.setattr("fastzip.storage.ensure_buckets", ensure)
    monkeypatch.setattr("time.sleep", sleep)
    if becomes_ready:
        runpy.run_path(str(COMPOSE_INIT))
    else:
        with pytest.raises(EndpointConnectionError):
            runpy.run_path(str(COMPOSE_INIT))
    assert ensure.call_count == (2 if becomes_ready else 30)
    assert sleep.call_args_list == [call(2)] * (ensure.call_count - 1)
