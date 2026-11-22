"""Media admission boundaries; FFprobe metadata is stubbed, image decoding is real.

Sparse files exercise the actual byte limits without allocating 100 MiB buffers.
The existing test_media.py suite separately exercises real FFmpeg/FFprobe.
"""

import json
import subprocess
from copy import deepcopy
from unittest.mock import Mock

import pytest
from fastzip import media, worker
from fastzip.db import session_factory
from fastzip.models import Job
from PIL import Image

from tests.conftest import upload

MIB = 1024 * 1024


@pytest.fixture
def mp4(tmp_path, monkeypatch):
    path = tmp_path / "source.mp4"
    path.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 52)
    metadata = {
        "streams": [{"codec_type": "video", "width": 1920, "height": 1080}],
        "format": {"duration": "120.0", "format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
    }
    probe = Mock()
    probe.side_effect = lambda *args, **kwargs: subprocess.CompletedProcess(
        args[0], 0, stdout=json.dumps(metadata).encode(), stderr=b""
    )
    monkeypatch.setattr(media.subprocess, "run", probe)
    return path, metadata, probe


@pytest.mark.parametrize("size", [20 * MIB - 1, 20 * MIB, 20 * MIB + 1])
def test_image_byte_limit_is_inclusive(tmp_path, image, size):
    path = tmp_path / "photo.png"
    path.write_bytes(image)
    with path.open("r+b") as file:
        file.truncate(size)
    if size > 20 * MIB:
        with pytest.raises(media.InvalidMedia, match="20 MB"):
            media.validate(path)
    else:
        assert media.validate(path) == ("image", ".png", "image/png")


@pytest.mark.parametrize("size", [100 * MIB - 1, 100 * MIB, 100 * MIB + 1])
def test_video_byte_limit_is_inclusive(mp4, size):
    path, _, probe = mp4
    with path.open("r+b") as file:
        file.truncate(size)
    if size > 100 * MIB:
        with pytest.raises(media.InvalidMedia, match="100 MB"):
            media.validate(path)
        probe.assert_not_called()
    else:
        assert media.validate(path) == ("video", ".mp4", "video/mp4")
        probe.assert_called_once()


@pytest.mark.parametrize("dimensions", [(4999, 5001), (5000, 5000), (5000, 5001)])
def test_image_pixel_limit_is_inclusive(tmp_path, dimensions):
    path = tmp_path / "large.png"
    with Image.new("RGB", dimensions, (20, 40, 60)) as image:
        image.save(path)
    if dimensions[0] * dimensions[1] > 25_000_000:
        with pytest.raises(media.InvalidMedia):
            media.validate(path)
    else:
        assert media.validate(path)[0] == "image"


@pytest.mark.parametrize(
    ("duration", "accepted"),
    [
        ("0", False),
        ("-0.001", False),
        ("119.999", True),
        ("120", True),
        ("120.001", False),
        ("NaN", False),
        ("Infinity", False),
        ("-Infinity", False),
    ],
)
def test_video_duration_boundaries(mp4, duration, accepted):
    path, metadata, _ = mp4
    metadata["format"]["duration"] = duration
    if accepted:
        assert media.validate(path)[0] == "video"
    else:
        with pytest.raises(media.InvalidMedia, match="2 minutes"):
            media.validate(path)


@pytest.mark.parametrize(
    ("dimensions", "accepted"),
    [
        ((1920, 1080), True),
        ((1080, 1920), True),
        ((1919, 1079), True),
        ((1921, 1080), False),
        ((1080, 1921), False),
        ((1920, 1081), False),
        ((1081, 1920), False),
        ((0, 1080), False),
        ((1920, -1), False),
    ],
)
def test_video_resolution_boundaries_in_both_orientations(mp4, dimensions, accepted):
    path, metadata, _ = mp4
    metadata["streams"][0].update(width=dimensions[0], height=dimensions[1])
    if accepted:
        assert media.validate(path)[0] == "video"
    else:
        with pytest.raises(media.InvalidMedia, match="1080p"):
            media.validate(path)


@pytest.mark.parametrize("video_count", [0, 1, 2])
def test_exactly_one_video_stream_is_required(mp4, video_count):
    path, metadata, _ = mp4
    stream = metadata["streams"][0]
    metadata["streams"] = [deepcopy(stream) for _ in range(video_count)]
    metadata["streams"].append({"codec_type": "audio"})
    if video_count == 1:
        assert media.validate(path)[0] == "video"
    else:
        with pytest.raises(media.InvalidMedia, match="one video stream"):
            media.validate(path)


@pytest.mark.parametrize(
    "error",
    [
        subprocess.TimeoutExpired("ffprobe", 15),
        subprocess.CalledProcessError(1, "ffprobe", stderr=b"private decoder details"),
        FileNotFoundError("private installation path"),
    ],
    ids=["timeout", "decoder-failed", "executable-missing"],
)
def test_ffprobe_failures_become_safe_validation_errors(mp4, error):
    path, _, probe = mp4
    probe.side_effect = error
    with pytest.raises(media.InvalidMedia, match="^Cannot read MP4 metadata$"):
        media.validate(path)


@pytest.mark.parametrize(
    "raw",
    [b"", b"{", b"{}", b"null", b"[]", b'{"streams": null, "format": {}}'],
    ids=["empty", "invalid-json", "missing-fields", "null-root", "list-root", "null-streams"],
)
def test_unusable_ffprobe_documents_are_rejected(mp4, raw):
    path, _, probe = mp4
    probe.side_effect = None
    probe.return_value = subprocess.CompletedProcess(["ffprobe"], 0, stdout=raw)
    with pytest.raises(media.InvalidMedia, match="^Cannot read MP4 metadata$"):
        media.validate(path)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing-duration",
        "null-duration",
        "null-width",
        "null-stream",
        "missing-width",
        "infinite-width",
        "wrong-container",
    ],
)
def test_invalid_ffprobe_fields_are_rejected(mp4, mutation):
    path, metadata, _ = mp4
    if mutation == "missing-duration":
        metadata["format"].pop("duration")
    elif mutation == "null-duration":
        metadata["format"]["duration"] = None
    elif mutation == "null-width":
        metadata["streams"][0]["width"] = None
    elif mutation == "null-stream":
        metadata["streams"] = [None]
    elif mutation == "missing-width":
        metadata["streams"][0].pop("width")
    elif mutation == "infinite-width":
        metadata["streams"][0]["width"] = float("inf")
    else:
        metadata["format"]["format_name"] = "matroska,webm"
    with pytest.raises(media.InvalidMedia):
        media.validate(path)


def test_truncated_jpeg_is_rejected_during_decode(tmp_path):
    path = tmp_path / "truncated.jpg"
    with Image.new("RGB", (64, 64), "green") as image:
        image.save(path, "JPEG")
    path.write_bytes(path.read_bytes()[:-30])
    with pytest.raises(media.InvalidMedia):
        media.validate(path)


def test_encoding_timeout_requeues_job_without_deleting_original(
    client, account, image, s3, monkeypatch
):
    assert upload(client, image).status_code == 202
    job = worker.claim("timeout-boundary-worker")
    assert job is not None

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(worker.subprocess, "run", timeout)
    worker.process(job, "timeout-boundary-worker")
    with session_factory()() as db:
        persisted = db.get(Job, job.id)
        assert persisted.status == "QUEUED"
        assert persisted.attempts == 1
        assert persisted.lease_until is None
        assert persisted.output_key is None
    assert s3.exists(job.original_key)
    assert not list(s3.objects("outputs/"))
