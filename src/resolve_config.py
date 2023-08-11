from __future__ import annotations

from pathlib import Path
from typing import Annotated
import copy

from annotated_types import Gt, Ge, Interval

import pydantic


class Config(pydantic.BaseModel):
    name: str
    continuation_model: str = "code-davinci-002"
    continuation_max_tokens: Annotated[int, Ge(0)] = 120
    representation_model: str = "intfloat/e5-large-v2"
    message_history_header: str | None = None  # todo: rename
    prompt_separator: str = "***"
    recency_window: Annotated[int, Gt(0)] = 20
    message_format: str = (
        "irc"  # todo: validate this and make it a MessageFormat object
    )

    temperature: Annotated[float, Ge(0)] = 0.9
    top_p: Annotated[float, Interval(gt=0, le=1)] = 0.97
    frequency_penalty: float = 0.75
    presence_penalty: float = 2.0
    stop_sequences: list[str] = []

    discord_mute: str | bool = False
    thread_mute: bool = True
    vendors: dict[str, SingleVendorConfig] = {}
    discord_token: str | None = None
    em_folder: Path


class LegacyConfig(Config):
    representation_model: str = "sentence-transformers/all-mpnet-base-v2"
    top_p: Annotated[float, Interval(gt=0, le=1)] = 0.7
    prompt_separator: str = "###"


class SingleVendorConfig(pydantic.BaseModel):
    config: dict = {}
    provides: list[str] = []

    def __getitem__(self, item):
        return getattr(self, item)


def get_defaults(model):
    defaults = {}
    for field in model.model_json_schema()["properties"].values():
        if "default" in field:
            defaults[field["title"]] = field["default"]
    return defaults


ALIASES = {
    "engines.complete": "continuation-model",
    "sampling": {
        "temperature": "temperature",
        "top_p": "top_p",
    },
    "chat.context": "message_history_header",
}

DEFAULTS = get_defaults(Config)
LEGACY_DEFAULTS = {**copy.deepcopy(DEFAULTS), **get_defaults(LegacyConfig)}


def override_with(items: dict, updates: dict):
    """
    Updates a dict, if a value is a dict, uses recursion.

    Returns:
        dict: a new dictionary with updated values
    """
    result = dict(items)
    for key, value in updates.items():
        if isinstance(value, dict):
            if key not in result:
                result[key] = {}
            result[key] = override_with(result[key], value)
        else:
            result[key] = value
    return result


def rename_keys(kv: dict, aliases: dict):
    new_kv = {}
    for key, value in kv.items():
        renamed = key.replace("-", "_")
        if renamed != key and renamed in kv:
            raise ValueError(f"Duplicate config keys: {key} and {renamed} both set")
        else:
            new_kv[renamed] = value
    for key, value in aliases.items():
        if key in new_kv:
            if value in new_kv:
                raise ValueError(f"Duplicate config keys: {value} and {key} both set")
            elif isinstance(value, str):
                new_kv[value.replace("-", "_")] = new_kv.pop(key)
            elif isinstance(value, dict):
                new_kv = override_with(new_kv, rename_keys(new_kv[key], value))
            else:
                raise ValueError(f"Invalid alias: {key} -> {value}")
    return new_kv


if rename_keys(DEFAULTS, ALIASES) != DEFAULTS:
    raise ValueError("Default config keys shouldn't use aliases")


def load_config_from_kv(kv: dict, defaults: dict = DEFAULTS) -> Config:
    dictionary = override_with(defaults, rename_keys(kv, ALIASES))
    return Config(**dictionary)
