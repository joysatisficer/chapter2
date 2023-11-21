from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any
from math import inf
import copy

from annotated_types import Gt, Ge, Interval

from pydantic import BaseModel, SerializeAsAny, field_validator
from pydantic.functional_validators import BeforeValidator
from message_formats import MessageFormat, MESSAGE_FORMAT_REGISTRY


class FacultyConfig(BaseModel):
    faculty: str
    format: MessageFormat
    separator: str = "***\n"
    max_tokens: int | float = inf
    header: str = ""
    footer: str = "***"
    recent_message_attention: int

    @field_validator("max_tokens")
    def check_integer_or_inf(cls, v):
        if isinstance(v, int) or v == inf:
            return v
        raise ValueError('Value must be an integer or float("inf")')


FACULTY_TO_CONFIG_CLASS: dict[str, type[FacultyConfig]] = {}


def faculty_name(faculty_name_str: str):
    def faculty_name_inner(cls):
        FACULTY_TO_CONFIG_CLASS[faculty_name_str] = cls
        return cls

    return faculty_name_inner


@faculty_name("metaphor_search")
class MetaphorSearchFacultyConfig(FacultyConfig):
    format: MessageFormat = MESSAGE_FORMAT_REGISTRY["web_document"]
    include_domains: list[str] | None = None
    exclude_domains: list[str] | None = None
    # set defaults
    max_tokens: int | float = 4000
    footer: str = "\n***\n"
    recent_message_attention: int = 5


@faculty_name("character")
class CharacterFacultyConfig(FacultyConfig):
    # set defaults
    format: MessageFormat = MESSAGE_FORMAT_REGISTRY["irc"]
    recent_message_attention: int = 7


FACULTY_ALIASES = {}


def parse_ensemble(ensemble: dict[str, Any]) -> FacultyConfig:
    if "faculty" in ensemble:
        faculty_name = ensemble["faculty"]
        if FacultyConfigCls := FACULTY_TO_CONFIG_CLASS.get(faculty_name):
            return FacultyConfigCls(**rename_keys(ensemble, FACULTY_ALIASES))
        else:
            raise ValueError(f"unknown faculty {faculty_name}")
    else:
        raise ValueError("non-faculty ensembles aren't implemented yet")


class Config(BaseModel):
    name: str
    continuation_model: str = "code-davinci-002"
    continuation_max_tokens: Annotated[int, Ge(0)] = 120
    representation_model: str = "intfloat/e5-large-v2"
    # todo: format validator and type
    message_history_format: MessageFormat = MESSAGE_FORMAT_REGISTRY["irc"]
    message_history_header: str = ""  # todo: rename
    scene_break: str = "***\n"  # todo: rename to scene_break_string
    recency_window: Annotated[int, Gt(0)] = 20
    """
    ensembles:
     - faculty: character
       max_tokens: 500

    [[ensembles]]
    faculty = "character"
    max_tokens = 500
    """
    ensembles: list[Annotated[SerializeAsAny[FacultyConfig], BeforeValidator(parse_ensemble)]] = []
    prevent_scene_break: bool = (
        False  # not the same thing as suppress_topic_break (prevent_gpt_topic_change
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
    metaphor_search_api_key: str | None = None
    em_folder: Path


class LegacyConfig(Config):
    representation_model: str = "sentence-transformers/all-mpnet-base-v2"
    top_p: Annotated[float, Interval(gt=0, le=1)] = 0.7
    scene_break: str = "###\n"


class SingleVendorConfig(BaseModel):
    config: dict = {}
    provides: list[str] = []

    def __getitem__(self, item):
        return getattr(self, item)


REHEARSAL_CONFIG = {"vendors": {"fake-local": SingleVendorConfig(provides=[".*"])}}


def get_defaults(model):
    defaults = {}
    for name, field in model.model_json_schema()["properties"].items():
        if "default" in field:
            defaults[name] = field["default"]
    return defaults


ALIASES = {
    "NAME": "name",
    "engines.complete": "continuation-model",
    "sampling": {
        "temperature": "temperature",
        "top_p": "top_p",
    },
    "chat.context": "message_history_header",
    "lookup_msg_cache": "character_faculty_recent_message_attention",
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
