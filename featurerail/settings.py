from __future__ import annotations

from dataclasses import asdict, dataclass

from PySide6.QtCore import QSettings


@dataclass
class AppSettings:
    language: str = "en"
    auto_crop: bool = True
    rail_mode: bool = True
    rail_strength: int = 92
    output_preset: str = "Match source"
    custom_width: int = 1920
    custom_height: int = 1080
    codec: str = "H.264"
    encoder: str = "Auto"
    quality: int = 21
    tracker: str = "KLT · CPU"

    @classmethod
    def load(cls) -> "AppSettings":
        store = QSettings("FeatureRail", "FeatureRail")
        defaults = cls()
        values: dict[str, object] = {}
        for key, default in asdict(defaults).items():
            raw = store.value(key, default)
            try:
                if isinstance(default, bool):
                    values[key] = raw if isinstance(raw, bool) else str(raw).lower() in {"true", "1", "yes"}
                elif isinstance(default, int):
                    values[key] = int(raw)
                elif isinstance(default, float):
                    values[key] = float(raw)
                else:
                    values[key] = str(raw)
            except (TypeError, ValueError):
                values[key] = default
        return cls(**values)

    def save(self) -> None:
        store = QSettings("FeatureRail", "FeatureRail")
        for key, value in asdict(self).items():
            store.setValue(key, value)
        store.sync()

    def output_size(self, source_width: int, source_height: int) -> tuple[int, int]:
        if self.output_preset == "Match source":
            return max(2, source_width - source_width % 2), max(2, source_height - source_height % 2)
        if self.output_preset == "Custom":
            height = max(144, int(self.custom_height))
            width = max(256, int(self.custom_width))
            return width + width % 2, height + height % 2
        heights = {"2160p": 2160, "1440p": 1440, "1080p": 1080, "720p": 720}
        height = heights.get(self.output_preset, source_height)
        width = round(height * source_width / max(1, source_height))
        return width + width % 2, height + height % 2
