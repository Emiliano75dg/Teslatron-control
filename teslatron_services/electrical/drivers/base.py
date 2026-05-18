from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ElectricalInstrumentDriver(ABC):
    def connect(self) -> None:
        return None

    def shutdown(self) -> None:
        return None

    def configure(self, config: dict[str, Any]) -> None:
        return None

    @abstractmethod
    def measure(self) -> dict[str, Any]:
        raise NotImplementedError
