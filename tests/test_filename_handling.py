"""Check the filenames an owner sees in job history and receives on download."""

import subprocess
from urllib.parse import parse_qs, quote, urlsplit

import pytest
from fastzip.config import settings
from fastzip.worker import claim, process
from urllib3.filepost import encode_multipart_formdata


def assert_job_filenames(client, file_part, original_name, result_name):
    # This encoder preserves literal tabs and filename="" in the multipart file part.
    body, content_type = encode_multipart_formdata({"file": file_part})
    upload = client.post("/api/jobs", content=body, headers={"Content-Type": content_type})
    assert upload.status_code == 202
    submitted = upload.json()
    assert submitted["filename"] == original_name

    job = claim()
    assert job is not None and str(job.id) == submitted["id"]
    process(job)

    history = client.get("/api/jobs")
    assert history.status_code == 200
    [entry] = history.json()["items"]
    assert entry["id"] == submitted["id"]
    assert entry["status"] == "SUCCEEDED"
    assert entry["filename"] == original_name

    for kind, expected in (("original", original_name), ("result", result_name)):
        response = client.get(f"/api/jobs/{job.id}/download", params={"kind": kind})
        assert response.status_code == 200
        download = response.json()
        assert download["is_original"] is (kind == "original")
        assert download["filename"] == expected
        query = parse_qs(urlsplit(download["url"]).query)
        assert query["response-content-disposition"] == [
            "attachment; filename*=UTF-8''" + quote(expected, safe="")
        ]


@pytest.mark.parametrize(
    "submitted, original, result",
    [
        pytest.param("albums/day/trip.v2.png", "trip.v2.png", "trip.v2.webp", id="posix-path"),
        pytest.param(r"albums\day\photo.png", "photo.png", "photo.webp", id="windows-path"),
        pytest.param(
            "field\tno\x7fte.png", "fieldnote.png", "fieldnote.webp", id="control-characters"
        ),
        pytest.param(
            "旅行 + 100% #1.png",
            "旅行 + 100% #1.png",
            "旅行 + 100% #1.webp",
            id="unicode-and-punctuation",
        ),
        pytest.param(
            "albums/" + "图" * 156 + "\t.png",
            "图" * 156 + ".png",
            "图" * 156 + ".webp",
            id="exactly-160-after-cleaning",
        ),
        pytest.param(
            "图" * 157 + ".png", "图" * 157 + ".pn", "图" * 157 + ".webp", id="161-characters"
        ),
        pytest.param("albums/day/", "upload", "upload.webp", id="empty-basename"),
        pytest.param("\t\x7f", "upload", "upload.webp", id="only-control-characters"),
        pytest.param("", "upload", "upload.webp", id="empty-filename"),
    ],
)
def test_image_filename_in_history_and_downloads(
    client, account, image, submitted, original, result
):
    assert_job_filenames(client, (submitted, image, "image/png"), original, result)


@pytest.mark.video
def test_video_filename_in_history_and_downloads(client, account, tmp_path):
    source = tmp_path / "source.mp4"
    # A short lossless source produces a smaller encoded result for the result-download branch.
    subprocess.run(
        [
            settings().ffmpeg_bin,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=128x96:rate=8:duration=1",
            "-c:v",
            "libx264",
            "-crf",
            "0",
            str(source),
        ],
        check=True,
    )
    assert_job_filenames(
        client,
        ("clips/短片 (1).MP4", source.read_bytes(), "video/mp4"),
        "短片 (1).MP4",
        "短片 (1).mp4",
    )
