# Licensed under CC BY-NC-SA 4.0. Strictly non-commercial.
import json
from dataclasses import asdict, dataclass, field


class ConfigError(Exception):
    pass


@dataclass(slots=True)
class WindowLegacy:
    hour_open: int = 6
    hour_close: int = 18


@dataclass(slots=True)
class WindowSun:
    sunrise_offset_min: int = -30
    sunset_offset_min: int = 30


@dataclass(slots=True)
class WindowConfig:
    mode: str = "sun_position"
    legacy: WindowLegacy = field(default_factory=WindowLegacy)
    sun: WindowSun = field(default_factory=WindowSun)


@dataclass(slots=True)
class OverrideOpenConfig:
    mode: str = "dynamic"
    fixed_hour: int = 8
    after_sunrise_min: int = 120


@dataclass(slots=True)
class OverrideCloseConfig:
    mode: str = "fixed"
    fixed_hour: int = 22
    after_sunset_min: int = 120


@dataclass(slots=True)
class LightConfig:
    lux_open: float = 8.0
    lux_close: float = 3.0


@dataclass(slots=True)
class SafetyConfig:
    move_timeout_s: int = 21


@dataclass(slots=True)
class Config:
    window: WindowConfig = field(default_factory=WindowConfig)
    override_open: OverrideOpenConfig = field(default_factory=OverrideOpenConfig)
    override_close: OverrideCloseConfig = field(default_factory=OverrideCloseConfig)
    light: LightConfig = field(default_factory=LightConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)


def default_config() -> Config:
    return Config()


def _to_dict(cfg: Config) -> dict:
    return asdict(cfg)


def _from_dict(data: dict) -> Config:
    w = data.get("window", {})
    legacy_d = w.get("legacy", {})
    sun_d = w.get("sun", {})
    oo = data.get("override_open", {})
    oc = data.get("override_close", {})
    light_d = data.get("light", {})
    safety_d = data.get("safety", {})

    return Config(
        window=WindowConfig(
            mode=w.get("mode", "sun_position"),
            legacy=WindowLegacy(
                hour_open=legacy_d.get("hour_open", 6),
                hour_close=legacy_d.get("hour_close", 18),
            ),
            sun=WindowSun(
                sunrise_offset_min=sun_d.get("sunrise_offset_min", -30),
                sunset_offset_min=sun_d.get("sunset_offset_min", 30),
            ),
        ),
        override_open=OverrideOpenConfig(
            mode=oo.get("mode", "dynamic"),
            fixed_hour=oo.get("fixed_hour", 8),
            after_sunrise_min=oo.get("after_sunrise_min", 120),
        ),
        override_close=OverrideCloseConfig(
            mode=oc.get("mode", "fixed"),
            fixed_hour=oc.get("fixed_hour", 22),
            after_sunset_min=oc.get("after_sunset_min", 120),
        ),
        light=LightConfig(
            lux_open=light_d.get("lux_open", 8.0),
            lux_close=light_d.get("lux_close", 3.0),
        ),
        safety=SafetyConfig(
            move_timeout_s=safety_d.get("move_timeout_s", 21),
        ),
    )


def load(path: str) -> Config:
    with open(path) as f:
        data = json.load(f)
    return _from_dict(data)


def save(cfg: Config, path: str) -> None:
    with open(path, "w") as f:
        json.dump(asdict(cfg), f, indent=2)


def validate_config(
    cfg: Config,
    now_local_min: int,
    rise_cet: int,
    set_cet: int,
    dst_offset: int,
) -> None:
    wo = window_open_local(cfg, rise_cet, dst_offset)
    wc = window_close_local(cfg, set_cet, dst_offset)
    ao = abs_open_local(cfg, rise_cet, dst_offset)
    ac = abs_close_local(cfg, set_cet, dst_offset)

    if ao < wo:
        raise ConfigError(
            f"abs_open ({ao // 60:02d}:{ao % 60:02d}) < window_open ({wo // 60:02d}:{wo % 60:02d})"
        )
    if ac < wc:
        raise ConfigError(
            f"abs_close ({ac // 60:02d}:{ac % 60:02d}) < "
            f"window_close ({wc // 60:02d}:{wc % 60:02d})"
        )
    if ao >= ac:
        raise ConfigError(
            f"abs_open ({ao // 60:02d}:{ao % 60:02d}) >= abs_close ({ac // 60:02d}:{ac % 60:02d})"
        )


def window_open_local(cfg: Config, rise_cet: int, dst: int) -> int:
    if cfg.window.mode == "sun_position":
        return rise_cet + dst + cfg.window.sun.sunrise_offset_min
    return cfg.window.legacy.hour_open * 60


def window_close_local(cfg: Config, set_cet: int, dst: int) -> int:
    if cfg.window.mode == "sun_position":
        return set_cet + dst + cfg.window.sun.sunset_offset_min
    return cfg.window.legacy.hour_close * 60


def abs_open_local(cfg: Config, rise_cet: int, dst: int) -> int:
    if cfg.override_open.mode == "dynamic":
        return rise_cet + dst + cfg.override_open.after_sunrise_min
    return cfg.override_open.fixed_hour * 60


def abs_close_local(cfg: Config, set_cet: int, dst: int) -> int:
    if cfg.override_close.mode == "dynamic":
        return set_cet + dst + cfg.override_close.after_sunset_min
    return cfg.override_close.fixed_hour * 60
