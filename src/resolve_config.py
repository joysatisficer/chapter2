from __future__ import annotations
from typing import TYPE_CHECKING, Union, Dict, List, Optional


import pydantic


class Config(pydantic.BaseModel):
    name: str = ""
    continuation_model: str = "code-davinci-002"
    continuation_max_tokens: pydantic.PositiveInt = 120
    discord_mute: Union[str, bool] = False
    thread_mute: bool = True
    recency_window: pydantic.PositiveInt = 20
    temperature: pydantic.PositiveFloat = 0.9
    top_p: pydantic.PositiveFloat = 0.7
    frequency_penalty: float = 0.75
    presence_penalty: float = 2.0
    vendors: Dict[str, SingleVendorConfig] = {}
    discord_token: Optional[str] = None


class SingleVendorConfig(pydantic.BaseModel):
    config: dict = {}
    provides: List[str] = []

    def __getitem__(self, item):
        return getattr(self, item)


ALIASES = {
    "engines.complete": "continuation-model",
    "sampling": {
        "temperature": "temperature",
        "top_p": "top-p",
    },
}

DEFAULTS = Config()


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
                new_kv[value] = new_kv.pop(key)
            elif isinstance(value, dict):
                new_kv = override_with(new_kv, rename_keys(new_kv[key], value))
            else:
                raise ValueError(f"Invalid alias: {key} -> {value}")
    return new_kv


if rename_keys(DEFAULTS.model_dump(), ALIASES) != DEFAULTS.model_dump():
    raise ValueError("Default config keys shouldn't use aliases")


def load_config_from_kv(kv: dict, defaults: Config = DEFAULTS) -> Config:
    dictionary = override_with(defaults.model_dump(), rename_keys(kv, ALIASES))
    return Config(**dictionary)
