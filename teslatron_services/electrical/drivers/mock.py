from __future__ import annotations

from random import random

from ..config import InstrumentConfig
from .base import ElectricalInstrumentDriver


class MockElectricalDriver(ElectricalInstrumentDriver):
    def __init__(self, config: InstrumentConfig):
        self.config = config
        self.connected = False

    def connect(self) -> None:
        self.connected = True

    def shutdown(self) -> None:
        self.connected = False

    def measure(self) -> dict[str, float | str]:
        base_value = self.config.mock.base_value
        noise_fraction = self.config.mock.noise_fraction
        factor = 1.0 + ((random() * 2.0) - 1.0) * noise_fraction
        return {
            "value": base_value * factor,
            "unit": self.config.mock.unit,
        }
