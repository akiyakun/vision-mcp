import os
from dataclasses import dataclass
from pathlib import Path


def integer(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
        if minimum <= value <= maximum:
            return value
    except ValueError:
        pass
    raise ValueError(f"{name} の設定範囲は {minimum}〜{maximum} です。")


@dataclass(frozen=True)
class Settings:
    image_root: Path
    model: str
    output_tokens: int
    usage_db: Path
    calls_per_day: int
    calls_per_window: int

    @classmethod
    def from_env(cls):
        return cls(
            Path(os.environ.get("VISION_IMAGE_ROOT", "/images")),
            os.environ.get("OPENAI_MODEL", "gpt-4.1-mini"),
            integer("VISION_MAX_OUTPUT_TOKENS", 800, 128, 2000),
            Path(os.environ.get("VISION_USAGE_DB", "/data/usage.sqlite3")),
            integer("VISION_MAX_CALLS_PER_DAY", 20, 1, 1000),
            integer("VISION_MAX_CALLS_PER_10_MIN", 3, 1, 100),
        )
