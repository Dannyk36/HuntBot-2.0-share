from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class Profile(BaseModel):
    roles: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    remote_only: bool = False
    min_salary: int | None = None


class Filters(BaseModel):
    exclude_keywords: list[str] = Field(default_factory=list)
    require_sponsorship: bool | None = None


class Runtime(BaseModel):
    default_mode: Literal["headed", "headless"] = "headed"
    max_retries: int = 2


class HuntBotConfig(BaseModel):
    profile: Profile = Field(default_factory=Profile)
    portals: list[str] = Field(default_factory=list)
    filters: Filters = Field(default_factory=Filters)
    runtime: Runtime = Field(default_factory=Runtime)


def load_config(path: str | Path) -> HuntBotConfig:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config not found: {p}")
    raw = yaml.safe_load(p.read_text()) or {}
    return HuntBotConfig.model_validate(raw)
