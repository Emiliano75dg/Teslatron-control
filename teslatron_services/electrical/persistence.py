from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
import json


class JsonlMeasurementWriter:
    def __init__(self, save_dir: str):
        self.save_dir = Path(save_dir)

    def append_event(self, run_id: str, event: dict[str, Any]) -> Path:
        path = self.run_path(run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=True) + "\n")
        return path

    def run_path(self, run_id: str) -> Path:
        today = datetime.now().strftime("%Y-%m-%d")
        return self.save_dir / today / f"{_slug(run_id)}.jsonl"


def _slug(value: str) -> str:
    chars = []
    for char in value.strip():
        if char.isalnum():
            chars.append(char.lower())
        elif char in {"-", "_"}:
            chars.append(char)
        elif char.isspace():
            chars.append("_")
    slug = "".join(chars).strip("_")
    return slug[:80] or "run"
