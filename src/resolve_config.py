from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal, Union
from math import inf
import copy

from annotated_types import Gt, Ge, Interval

from pydantic import BaseModel, field_validator, Field
from message_formats import MessageFormat, MESSAGE_FORMAT_REGISTRY


class FacultyConfig(BaseModel):
    faculty: str
    format: MessageFormat
    separator: str = "***\n"
    max_tokens: int | float = inf  # todo: parsing inf from yaml
    header: str = ""
    footer: str = "***"
    # todo: replace with perplexity-chunking based method
    recent_message_attention: int

    @field_validator("max_tokens")
    def check_integer_or_inf(cls, v):
        if isinstance(v, int) or v == inf:
            return v
        raise ValueError('Value must be an integer or float("inf")')


class CharacterFacultyConfig(FacultyConfig):
    faculty: Literal["character"] = "character"
    # set defaults
    format: MessageFormat = MESSAGE_FORMAT_REGISTRY["irc"]
    recent_message_attention: int = 7


class MetaphorSearchFacultyConfig(FacultyConfig):
    faculty: Literal["metaphor_search"] = "metaphor_search"
    format: MessageFormat = MESSAGE_FORMAT_REGISTRY["web_document"]
    include_domains: list[str] | None = None
    exclude_domains: list[str] | None = None
    # set defaults
    max_tokens: int | float = 4000
    footer: str = "\n***\n"
    recent_message_attention: int = 5


EnsembleConfig = Annotated[
    CharacterFacultyConfig | MetaphorSearchFacultyConfig,
    Field(..., discriminator="faculty"),
]


class DiscordGenerateAvatarAddonConfig(BaseModel):
    name: Literal["generate_avatar"]
    prompt: str
    regenerate_every: float | None = None


class DiscordInterfaceConfig(BaseModel):
    name: Literal["discord"] = "discord"
    auth: str | None = None
    addons: list[Union[DiscordGenerateAvatarAddonConfig]] = []


class MikotoInterfaceConfig(BaseModel):
    name: Literal["mikoto"] = "mikoto"
    auth: str
    # todo: high-level API for customizing if a message should be engaged with
    allowed_users: list[str] | None = None
    # todo: config loaders and interfaces as separate things
    custom_config: dict = {}


class CompletionsInterfaceConfig(BaseModel):
    name: Literal["completions"] = "completions"


class ChatCompletionsInterfaceConfig(BaseModel):
    name: Literal["chatcompletions"] = "chatcompletions"
    default_name: str = "user"


InterfaceConfig = Annotated[
    DiscordInterfaceConfig
    | MikotoInterfaceConfig
    | CompletionsInterfaceConfig
    | ChatCompletionsInterfaceConfig,
    Field(..., discriminator="name"),
]


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
    ensembles: list[EnsembleConfig] = []
    prevent_scene_break: bool = (
        False  # not the same thing as suppress_topic_break (prevent_gpt_topic_change
    )
    prevent_gpt_topic_change: bool = False

    temperature: Annotated[float, Ge(0)] = 0.9
    top_p: Annotated[float, Interval(gt=0, le=1)] = 0.97
    frequency_penalty: float = 0.75
    presence_penalty: float = 2.0
    stop_sequences: list[str] = []

    interfaces: list[InterfaceConfig] = [DiscordInterfaceConfig()]
    discord_mute: str | bool = False
    thread_mute: bool = True
    vendors: dict[str, SingleVendorConfig] = {}
    metaphor_search_api_key: str | None = None
    em_folder: Path
    only_reply_when_mentioned: bool = False


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
# core end


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
    "active_interfaces": None,
    "discord_token": None,
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
        if value is None:
            continue
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
    if active_interfaces := kv.get("active_interfaces"):
        assert (
            kv.get("interfaces") is None
        ), "config key `interfaces` conflicts with legacy key `active_inferences`"
        interfaces = [{"name": interface_name} for interface_name in active_interfaces]
        kv["interfaces"] = interfaces
    elif kv.get("interfaces") is None:
        kv["interfaces"] = [{"name": "discord"}]
    if discord_token := kv.get("discord_token"):
        for interface in kv["interfaces"]:
            if interface["name"] == "discord":
                interface["auth"] = discord_token
                break
    dictionary = override_with(defaults, rename_keys(kv, ALIASES))
    return Config(**dictionary)
