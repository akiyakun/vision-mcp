"""Only explicitly named files below a dedicated, read-only image directory."""
import base64
import io
import os
import stat
import warnings
from pathlib import Path, PurePosixPath

from PIL import Image, ImageOps

MAX_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 20_000_000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


class ImageError(ValueError):
    pass


def read_image(root: Path, name: str) -> bytes:
    # No URLs, Windows paths, directory traversal, symlinks or special files.
    parts = name.split("/")
    if (not name or len(name) > 240 or "\\" in name or ":" in name or "\x00" in name
            or PurePosixPath(name).is_absolute()
            or any(p in ("", ".", "..") or p.startswith(".") for p in parts)):
        raise ImageError("画像専用フォルダー内の相対ファイル名を指定してください。")
    fd = None
    try:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(file_fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_BYTES:
                raise ImageError("画像は通常ファイルかつ10 MiB以下である必要があります。")
            data = stream.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ImageError("画像は10 MiB以下にしてください。")
        return data
    except OSError:
        raise ImageError("画像を読めません。ファイル名・共有フォルダー・読み取り権限を確認してください。") from None
    finally:
        if fd is not None:
            os.close(fd)


def prepare_image(root: Path, name: str, detail: str) -> str:
    data = read_image(root, name)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.format not in ("PNG", "JPEG", "WEBP", "GIF"):
                    raise ImageError("PNG / JPEG / WebP / 静止GIFを指定してください。")
                if getattr(image, "n_frames", 1) != 1:
                    raise ImageError("アニメーションは非対応です。必要な1フレームを保存してください。")
                if image.width * image.height > MAX_PIXELS:
                    raise ImageError("画像は2000万画素以下にしてください。")
                image.load()
                normalized = ImageOps.exif_transpose(image).convert("RGBA")
                # Composite transparency on white, strip all metadata, preserve text with PNG.
                canvas = Image.new("RGB", normalized.size, "white")
                canvas.paste(normalized, mask=normalized.getchannel("A"))
                edge = 512 if detail == "low" else 2048
                canvas.thumbnail((edge, edge), Image.Resampling.LANCZOS)
                out = io.BytesIO()
                canvas.save(out, format="PNG")
        return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode("ascii")
    except ImageError:
        raise
    except Exception:
        raise ImageError("画像が破損しているか、形式・画素数が非対応です。") from None
