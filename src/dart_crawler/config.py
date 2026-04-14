"""Configuration loaded from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    api_key: str
    request_timeout: int = 30
    request_delay: float = 0.5
    max_retries: int = 3
    data_dir: Path = Path("./data")
    output_dir: Path = Path("./output")

    @classmethod
    def from_env(cls) -> "Settings":
        api_key = os.getenv("DART_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError(
                "DART_API_KEY is not set. Copy .env.example to .env and fill it in."
            )
        return cls(
            api_key=api_key,
            request_timeout=int(os.getenv("REQUEST_TIMEOUT", "30")),
            request_delay=float(os.getenv("REQUEST_DELAY", "0.5")),
            max_retries=int(os.getenv("MAX_RETRIES", "3")),
            data_dir=Path(os.getenv("DATA_DIR", "./data")),
            output_dir=Path(os.getenv("OUTPUT_DIR", "./output")),
        )
