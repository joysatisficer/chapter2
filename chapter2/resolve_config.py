from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal, Union
from math import inf as infinity
import copy

import pydantic
from annotated_types import Gt, Ge, Interval

from pydantic import BaseModel, field_validator, Field
from pydantic_core import PydanticUndefined

from message_formats import MessageFormat, IRCMessageFormat, WebDocumentMessageFormat


class LayerOfEnsembleFormat(BaseModel):
    format: MessageFormat
    separator: str = "***\n"
    max_tokens: int | float = infinity  # todo: parsing inf from yaml
    max_items: int | float = infinity
    header: str = ""
    footer: str = "***"
    operator: Literal["prepend"] | Literal["append"] = "append"

    @field_validator("max_tokens")
    def check_integer_or_inf(cls, v):
        if isinstance(v, int) or v == infinity:
            return v
        raise ValueError('Value must be an integer or float("inf")')


EnsembleFormat = list[LayerOfEnsembleFormat]


class FacultyConfig(BaseModel):
    faculty: str
    input_format: MessageFormat = IRCMessageFormat()
    ensemble_format: EnsembleFormat
    recent_message_attention: int


class FixedSizeChunker(BaseModel):
    name: Literal["fixed"] = "fixed"
    n_lines: int = 3


class PerplexityChunker(BaseModel):
    name: Literal["perplexity"] = "perplexity"


class UltratrieverConfig(BaseModel):
    # nested array of steps with a type system
    # organize in order of steps
    chunker: FixedSizeChunker | PerplexityChunker = FixedSizeChunker()
    # message_reformat: pair of message formats
    # deduplication
    representation_model: str = "mixedbread-ai/mxbai-embed-large-v1"
    # metric: Literal["knn/euclid"] | Literal["hyperplane/"]
    ranking_metric: Literal["knn"] | Literal["svm"] = "knn"
    # deduplication
    # reranker


class CharacterFacultyConfig(FacultyConfig):
    faculty: Literal["character"] = "character"
    name: str | None = None  # defaults to config.em_name
    chunk_size: int = 3
    retriever: UltratrieverConfig = UltratrieverConfig()
    recent_message_attention: int = 7
    # set defaults
    ensemble_format: EnsembleFormat = [
        LayerOfEnsembleFormat(format=IRCMessageFormat(), operator="prepend"),
        LayerOfEnsembleFormat(
            format=IRCMessageFormat(), max_items=infinity, separator="", footer=""
        ),
    ]


class ExaSearchFullTextConfig(BaseModel):
    max_characters: int = 2500
    include_html_tags: bool = False


class ExaSearchHighlightsConfig(BaseModel):
    # todo: replace with full ensemble nesting
    highlights_per_url: int = 3
    sentences_per_highlight: int = 3


class ExaSearchFacultyConfig(FacultyConfig):
    faculty: Literal["metaphor_search"] | Literal["exa_search"] = "exa_search"
    include_domains: list[str] | None = None
    exclude_domains: list[str] | None = None
    max_results: int = 20  # 10 is the cap of the Wanderer plan
    use_autoprompt: bool = False
    output: ExaSearchHighlightsConfig = ExaSearchHighlightsConfig()
    # client-side filtering
    ignored_urls: list[str] = []
    # performance hints
    impl_hint_initial_num_results: int = 10
    # set defaults
    recent_message_attention: int = 5
    ensemble_format: EnsembleFormat = [
        LayerOfEnsembleFormat(
            format=WebDocumentMessageFormat(),
            max_tokens=4000,
            footer="***\n",
            recent_message_attention=5,
        )
    ]


EnsembleConfig = Annotated[
    CharacterFacultyConfig | ExaSearchFacultyConfig,
    Field(..., discriminator="faculty"),
]


class DiscordGenerateAvatarAddonConfig(BaseModel):
    name: Literal["generate_avatar"]
    image_vendor: Literal["novelai"] | Literal["openai"] = "novelai"
    image_model: str = "auto"
    prompt: str
    regenerate_every: float | None = None
    # image model parameters
    scale: float = 5.0

    def __init__(self, **data):
        super().__init__(**data)
        match self.image_vendor:
            case "novelai":
                self.image_model = "nai-diffusion-3"
            case "openai":
                self.image_model = "dall-e-3"


# todo: support sets (concatenate instead of override)


class DiscordInterfaceConfig(BaseModel):
    name: Literal["discord"] = "discord"
    auth: str | None = None
    addons: list[Union[DiscordGenerateAvatarAddonConfig]] = []
    proxy_url: str | None = None


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
    continuation_model: str = "meta-llama/Meta-Llama-3-70B"
    continuation_max_tokens: Annotated[int, Ge(0)] = 120
    # todo: update default representation model
    representation_model: str = "mixedbread-ai/mxbai-embed-large-v1"
    message_history_format: MessageFormat = IRCMessageFormat()
    message_history_header: str = ""  # todo: rename
    message_history_separator: str = ""
    message_history_footer: str = ""
    scene_break: str = "***\n"  # todo: rename to scene_break_string
    recency_window: Annotated[int, Gt(0)] = 20
    ensembles: list[EnsembleConfig] = []
    prevent_scene_break: bool = (
        False  # not the same thing as suppress_topic_break (prevent_gpt_topic_change
    )
    prevent_gpt_topic_change: bool = False

    temperature: Annotated[float, Ge(0)] = (
        0.9  # todo: vary on model; 0.9 for davinci-002, 1.0 for gpt-4-base
    )
    top_p: Annotated[float, Interval(gt=0, le=1)] = 0.98
    frequency_penalty: float = 0.75
    presence_penalty: float = 2.0
    stop_sequences: list[str] = []
    logit_bias: dict[int | str, float] = {}
    best_of: int = 1
    continuation_model_local_tokenization: bool = False
    continuation_options: dict = {}

    interfaces: list[InterfaceConfig] = [DiscordInterfaceConfig()]
    discord_mute: str | bool = False
    thread_mute: bool = False
    em_folder: Path
    reply_on_ping: bool = True
    reply_on_random: int | bool = 53
    reply_on_name: bool = True
    nicknames: list = []
    discord_send_typing: bool = True
    discord_random_threshold: float = 1.0

    # API keys
    vendors: dict[str, SingleVendorConfig] = {}
    exa_search_api_key: str | None = None
    sentry_dsn_url: str | None = None
    novelai_api_key: str | None = None

    end_to_end_test: bool = False
    end_to_end_test_discord_token: str | None = None
    end_to_end_test_discord_channel_id: int | None = None


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


# todo: support defaults versioning
def get_defaults(model: pydantic.BaseModel) -> dict:
    defaults = {}
    for name, field in model.model_fields.items():
        if field.default != PydanticUndefined:
            if isinstance(field.default, pydantic.BaseModel):
                defaults[name] = get_defaults(field.default)
            elif isinstance(field.default, list):
                newlist = []
                for item in field.default:
                    if isinstance(item, pydantic.BaseModel):
                        newlist.append(get_defaults(item))
                    else:
                        newlist.append(item)
                defaults[name] = newlist
            elif isinstance(field.default, dict):
                newdict = {}
                for key, value in field.default.items():
                    if isinstance(key, pydantic.BaseModel):
                        realkey = get_defaults(key)
                    else:
                        realkey = key
                    if isinstance(value, pydantic.BaseModel):
                        realvalue = get_defaults(value)
                    else:
                        realvalue = value
                    newdict[realkey] = realvalue
                defaults[name] = newdict
            else:
                defaults[name] = field.default
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
    "metaphor_search_api_key": "exa_search_api_key",
}

# todo: namespaced default sets to allow for opt-in defaults upgrades
DEFAULTS = get_defaults(Config)
LEGACY_DEFAULTS = {**copy.deepcopy(DEFAULTS), **get_defaults(LegacyConfig)}


def overlay(base: dict | list, updates: dict | list):
    result = copy.copy(base)
    if isinstance(updates, list):
        keyvalues = enumerate(updates)
    else:
        keyvalues = updates.items()
    if isinstance(base, list):
        keys = list(range(len(base)))
    else:
        keys = base.keys()
    for key, value in keyvalues:
        if isinstance(value, dict):
            # recurse inside dicts
            if key not in keys:
                if isinstance(result, list):
                    if key >= len(result):
                        result.append({})
                    else:
                        assert 0, "impossible"
                else:
                    result[key] = {}
            result[key] = overlay(result[key], value)
        elif isinstance(value, list):
            # recurse inside lists
            if key not in keys:
                if isinstance(result, list):
                    if key >= len(result):
                        result.append([])
                    else:
                        assert 0, "impossible"
                else:
                    result[key] = []
            result[key] = overlay(result[key], value)
        else:
            if isinstance(result, list):
                # assume lists of primitives act like sets
                result.append(value)
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
                new_kv = overlay(new_kv, rename_keys(new_kv[key], value))
            else:
                raise ValueError(f"Invalid alias: {key} -> {value}")
    return new_kv


if rename_keys(DEFAULTS, ALIASES) != DEFAULTS:
    raise ValueError("Default config keys shouldn't use aliases")


def load_config_from_kv(kv: dict | None, defaults: dict = DEFAULTS) -> Config:
    if kv is None:
        kv = {}
    if active_interfaces := kv.get("active_interfaces"):
        assert (
            kv.get("interfaces") is None
        ), "config key `interfaces` conflicts with legacy key `active_inferences`"
        interfaces = [{"name": interface_name} for interface_name in active_interfaces]
        kv["interfaces"] = interfaces
        del kv["active_interfaces"]
    if discord_token := kv.get("discord_token"):
        if "interfaces" not in kv:
            kv["interfaces"] = defaults["interfaces"]
        for interface in kv["interfaces"]:
            if interface["name"] == "discord":
                interface["auth"] = discord_token
                break
        del kv["discord_token"]
    if discord_proxy_url := kv.get("discord_proxy_url"):
        for interface in kv["interfaces"]:
            if interface["name"] == "discord":
                interface["proxy_url"] = discord_proxy_url
        del kv["discord_proxy_url"]
    dictionary = overlay(defaults, rename_keys(kv, ALIASES))
    return Config(**dictionary)
