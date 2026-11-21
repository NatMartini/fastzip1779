import json
import shutil
import subprocess

import pytest
from fastzip.config import settings
from fastzip.media import InvalidMedia, compress, validate
from PIL import Image


def test_webp_preserves_alpha_without_metadata(tmp_path):
    source = tmp_path / "input.png"
    Image.new("RGBA", (80, 80), (120, 80, 50, 100)).save(source)
    assert validate(source)[0] == "image"
    output, mime = compress(source, tmp_path, "image", "balanced")
    assert mime == "image/webp"
    with Image.open(output) as image:
        assert image.mode == "RGBA"
        assert not image.getexif()


@pytest.mark.video
def test_video_validation_and_encoding(tmp_path):
    s = settings()
    if not shutil.which(s.ffmpeg_bin) or not shutil.which(s.ffprobe_bin):
        pytest.fail("ffmpeg and ffprobe are required to verify the video worker")
    source = tmp_path / "input.mp4"
    subprocess.run(
        [
            s.ffmpeg_bin,
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x240:rate=24",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000",
            "-t",
            "1",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            str(source),
        ],
        check=True,
    )
    assert validate(source) == ("video", ".mp4", "video/mp4")
    output, mime = compress(source, tmp_path, "video", "smaller")
    assert mime == "video/mp4"
    metadata = json.loads(
        subprocess.check_output(
            [s.ffprobe_bin, "-v", "error", "-show_streams", "-of", "json", str(output)]
        )
    )
    assert {stream["codec_name"] for stream in metadata["streams"]} == {"h264", "aac"}
    assert validate(output)[0] == "video"


def test_invalid_mp4_and_animation_rejected(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"invalid" * 100)
    with pytest.raises(InvalidMedia):
        validate(bad)
    animated = tmp_path / "animated.png"
    frames = [Image.new("RGB", (10, 10), color) for color in ("red", "blue")]
    frames[0].save(animated, save_all=True, append_images=frames[1:], duration=100)
    with pytest.raises(InvalidMedia, match="Animated"):
        validate(animated)
