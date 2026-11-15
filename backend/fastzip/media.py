import json
import math
import subprocess
import warnings
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from fastzip.config import settings

MAX_UPLOAD = 100 * 1024 * 1024
MAX_IMAGE = 20 * 1024 * 1024
MAX_PIXELS = 25_000_000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS
PRESETS = {"smaller": (55, 32), "balanced": (75, 26), "higher_quality": (88, 21)}


class InvalidMedia(ValueError):
    pass


def validate(path: Path) -> tuple[str, str, str]:
    size = path.stat().st_size
    if size == 0 or size > MAX_UPLOAD:
        raise InvalidMedia("File must be nonempty and no larger than 100 MB")
    with path.open("rb") as file:
        header = file.read(64)
    if header.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n")):
        if size > MAX_IMAGE:
            raise InvalidMedia("Images must be no larger than 20 MB")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(path) as image:
                    if (
                        image.format not in {"JPEG", "PNG"}
                        or image.width * image.height > MAX_PIXELS
                    ):
                        raise InvalidMedia(
                            "Images must be JPEG/PNG and no larger than 25 megapixels"
                        )
                    if getattr(image, "n_frames", 1) != 1:
                        raise InvalidMedia("Animated images are not supported")
                    kind = image.format
                    image.verify()
                with Image.open(path) as image:
                    image.load()  # verify() alone does not reject truncated JPEG pixel data.
        except (
            OSError,
            SyntaxError,
            UnidentifiedImageError,
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
        ) as exc:
            raise InvalidMedia("Invalid or oversized image") from exc
        return (
            "image",
            ".jpg" if kind == "JPEG" else ".png",
            "image/jpeg" if kind == "JPEG" else "image/png",
        )
    if len(header) < 12 or header[4:8] != b"ftyp":
        raise InvalidMedia("Only JPEG, PNG, and MP4 files are supported")
    try:
        result = subprocess.run(
            [
                settings().ffprobe_bin,
                "-v",
                "error",
                "-protocol_whitelist",
                "file,pipe",
                "-show_format",
                "-show_streams",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            timeout=15,
            check=True,
        )
        metadata = json.loads(result.stdout)
        videos = [s for s in metadata["streams"] if s.get("codec_type") == "video"]
        duration = float(metadata["format"]["duration"])
        if len(videos) != 1 or not math.isfinite(duration) or not 0 < duration <= 120:
            raise InvalidMedia("MP4 must contain one video stream and be at most 2 minutes")
        width, height = int(videos[0]["width"]), int(videos[0]["height"])
        if min(width, height) <= 0 or max(width, height) > 1920 or min(width, height) > 1080:
            raise InvalidMedia("Video resolution must be at most 1080p")
        if not set(metadata["format"]["format_name"].split(",")) & {"mp4", "mov"}:
            raise InvalidMedia("Invalid MP4 container")
    except (subprocess.SubprocessError, ValueError, KeyError, OSError) as exc:
        if isinstance(exc, InvalidMedia):
            raise
        raise InvalidMedia("Cannot read MP4 metadata") from exc
    return "video", ".mp4", "video/mp4"


def compress(source: Path, directory: Path, media_type: str, preset: str) -> tuple[Path, str]:
    quality, crf = PRESETS[preset]
    if media_type == "image":
        output = directory / "result.webp"
        with Image.open(source) as image:
            image = ImageOps.exif_transpose(image)
            image = image.convert(
                "RGBA" if "A" in image.getbands() or "transparency" in image.info else "RGB"
            )
            image.save(output, "WEBP", quality=quality, method=4, exif=b"", icc_profile=b"")
        return output, "image/webp"
    output = directory / "result.mp4"
    subprocess.run(
        [
            settings().ffmpeg_bin,
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-protocol_whitelist",
            "file,pipe",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-map_metadata",
            "-1",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            str(crf),
            "-threads",
            "1",
            "-pix_fmt",
            "yuv420p",
            "-vf",
            "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(output),
        ],
        capture_output=True,
        timeout=settings().compression_timeout,
        check=True,
    )
    return output, "video/mp4"


if __name__ == "__main__":
    import sys

    compress(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4])
