from __future__ import annotations

import pprint
import types
import typing
from pathlib import Path
from typing import Annotated, Literal, Union, Type
from math import inf as infinity
import copy

import pydantic
from annotated_types import Gt, Ge, Interval

from pydantic import field_validator, SecretStr, Secret, BaseModel, Field
from pydantic_core import PydanticUndefined

from message_formats import (
    MessageFormat,
    IRCMessageFormat,
    WebDocumentMessageFormat,
    ColonMessageFormat,
)


class LayerOfEnsembleFormat(BaseModel):
    format: MessageFormat
    separator: str = "###\n"
    max_tokens: int | float = infinity  # todo: parsing inf from yaml
    max_items: int | float = infinity
    header: str = ""
    footer: str = "###\n"
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
    recent_message_attention: int | float | float


class FixedSizeChunker(BaseModel):
    name: Literal["fixed"] = "fixed"
    n_lines: int = 3


class UltratrieverConfig(BaseModel):
    # nested array of steps with a type system
    # organize in order of steps
    chunker: FixedSizeChunker = FixedSizeChunker()
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
    recent_message_attention: int | float = 7
    # set defaults
    ensemble_format: EnsembleFormat = [
        LayerOfEnsembleFormat(format=IRCMessageFormat(), operator="prepend"),
        LayerOfEnsembleFormat(
            format=IRCMessageFormat(), max_items=infinity, separator="", footer=""
        ),
    ]


class SimFacultyConfig(FacultyConfig):
    faculty: Literal["sim"] = "sim"
    em: EmConfig
    recent_message_attention: int | float = infinity
    ensemble_format: EnsembleFormat = [
        LayerOfEnsembleFormat(format=IRCMessageFormat(), operator="prepend"),
        LayerOfEnsembleFormat(
            format=IRCMessageFormat(), max_items=infinity, separator="", footer=""
        ),
    ]


class HistoryFacultyConfig(FacultyConfig):
    faculty: Literal["history"] = "history"
    filename: str = "history.txt"
    nickname: str | None = None
    nicknames: dict[str, str] = {}
    recent_message_attention: int | float = 0
    input_format: MessageFormat = ColonMessageFormat()
    ensemble_format: EnsembleFormat = [
        LayerOfEnsembleFormat(
            format=IRCMessageFormat(),
            operator="prepend",
            max_items=infinity,
            separator="",
            footer="",
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
    start_crawl_date: str | None = None
    end_crawl_date: str | None = None
    start_published_date: str | None = None
    end_published_date: str | None = None
    # client-side filtering
    ignored_urls: list[str] = []
    # performance hints
    impl_hint_initial_num_results: int = 10
    # set defaults
    recent_message_attention: int | float = 5
    ensemble_format: EnsembleFormat = [
        LayerOfEnsembleFormat(
            format=WebDocumentMessageFormat(),
            max_tokens=4000,
            footer="###\n",
            recent_message_attention=5,
        )
    ]


class AirtableConfig(pydantic.BaseModel):
    base_id: str
    table_id: str
    api_token: SecretStr


class AirtableNotesFacultyConfig(FacultyConfig):
    faculty: Literal["airtable_notes"] = "airtable_notes"
    recent_message_attention: int = 0
    airtable: AirtableConfig


EnsembleConfig = Annotated[
    CharacterFacultyConfig
    | HistoryFacultyConfig
    | ExaSearchFacultyConfig
    | SimFacultyConfig
    | AirtableNotesFacultyConfig,
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


class EmConfig(BaseModel):
    name: str
    emname: str
    continuation_model: str = "meta-llama/Meta-Llama-3.1-405B"
    continuation_max_tokens: Annotated[int, Ge(0)] = 120
    representation_model: str = "mixedbread-ai/mxbai-embed-large-v1"
    # todo: make message_history coequal with other ensembles
    message_history_max_tokens: int | float = infinity
    message_history_format: MessageFormat = IRCMessageFormat()
    message_history_header: str = ""
    message_history_separator: str = ""
    message_history_footer: str = ""
    message_history_operator: Literal["prepend"] | Literal["append"] = "prepend"
    scene_break: str = "###\n"  # todo: remove
    recency_window: Annotated[int, Gt(0)] = 35
    ensembles: list[EnsembleConfig] = []
    prevent_scene_break: bool = (
        False  # not the same thing as suppress_topic_break (prevent_gpt_topic_change
    )
    prevent_gpt_topic_change: bool = True
    total_max_tokens: int | float = 31_900
    name_prefix: bool = True
    name_prefix_optional: bool = True
    split_message: bool = True
    mufflers: list[str] = [
        "has_url",
        "has_pump_fun_ca",
        "has_img_url_token",
    ]  # "context_sentence_repetition"

    temperature: Annotated[float, Ge(0)] = 0.95  # todo: vary on model
    top_p: Annotated[float, Interval(gt=0, le=1)] = 0.995  # ditto
    # 0 until a way to adjust it automatically for long context windows is impl'd
    frequency_penalty: float = 0
    presence_penalty: float = 0
    stop_sequences: list[str] = []
    logit_bias: dict[int | str, float] = {}
    best_of: int | None = None
    continuation_model_local_tokenization: bool = False
    continuation_options: dict = {}

    # API keys
    vendors: Secret[dict[str, SingleVendorConfig]] = {}
    exa_search_api_key: SecretStr | None = None
    novelai_api_key: SecretStr | None = None

    folder: Path


# todo: support sets (concatenate instead of override)


class ReplyOnSimConfig(BaseModel):
    em_overrides: dict = {
        "continuation_model": "google/gemma-2-2b",
        "name_prefix": False,
        "name_prefix_optional": False,
        "ensembles": [],
        "continuation_max_tokens": 15,
        "prompt_max_tokens": 8000,
    }
    match: Literal["predict_username"] = "predict_username"


class SharedInterfaceConfig(BaseModel):
    name: str = ""
    mute: bool = False
    reply_on_ping: bool = True
    reply_on_random: int | bool = 53
    reply_on_name: bool = True
    nicknames: list = []  # for reply_on
    reply_on_sim: ReplyOnSimConfig | Literal[False] = False
    reply_on_regex: str | None = None
    ignore_dotted_messages: bool = True
    end_to_end_test: bool = False


class ImageLimits(BaseModel):
    max_width: int = 1568
    max_height: int = 1568
    max_images: int = 5


class DiscordInterfaceConfig(SharedInterfaceConfig):
    name: Literal["discord"] = "discord"
    discord_token: SecretStr | None = None
    addons: list[Union[DiscordGenerateAvatarAddonConfig]] = []
    discord_proxy_url: SecretStr | None = None
    threads_inherit_history: bool = True
    max_queued_replies: int = 2
    thread_mute: bool = False
    send_typing: bool = True
    end_to_end_test_discord_token: str | None = None
    end_to_end_test_discord_channel_id: int | None = None
    discord_user_whitelist: list[int] = []
    may_speak: list[str] = []
    include_images: bool = True
    image_limits: ImageLimits = ImageLimits()
    exo_enabled: bool = False
    airtable: AirtableConfig | None = None


class RPCInterfaceConfig(SharedInterfaceConfig):
    name: Literal["rpc"] = "rpc"


class MikotoInterfaceConfig(SharedInterfaceConfig):
    name: Literal["mikoto"] = "mikoto"
    mikoto_token: str
    # todo: high-level API for customizing if a message should be engaged with
    allowed_users: list[str] | None = None
    # todo: config loaders and interfaces as separate things
    custom_config: dict = {}


class CompletionsInterfaceConfig(SharedInterfaceConfig):
    name: Literal["completions"] = "completions"
    default_name: str = "user"
    port: int | None = None


class ChatCompletionsInterfaceConfig(SharedInterfaceConfig):
    name: Literal["chatcompletions"] = "chatcompletions"
    default_name: str = "user"
    port: int | None = None


class InfraInterfaceConfig(DiscordInterfaceConfig):
    name: Literal["infra"] = "infra"


InterfaceConfig = Annotated[
    DiscordInterfaceConfig
    | RPCInterfaceConfig
    | MikotoInterfaceConfig
    | CompletionsInterfaceConfig
    | ChatCompletionsInterfaceConfig
    | InfraInterfaceConfig,
    Field(..., discriminator="name"),
]


class Config(BaseModel):
    em: EmConfig
    interfaces: list[InterfaceConfig] = [DiscordInterfaceConfig()]


class LegacyConfig(Config):
    representation_model: str = "sentence-transformers/all-mpnet-base-v2"
    top_p: Annotated[float, Interval(gt=0, le=1)] = 0.7
    scene_break: str = "###\n"


class SingleVendorConfig(BaseModel):
    config: dict = {}
    provides: list[str] = []

    def __getitem__(self, item):
        return getattr(self, item)


# todo: support defaults versioning
def get_defaults(model: Type[pydantic.BaseModel]) -> dict:
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
    "metaphor_search_api_key": "exa_search_api_key",
    "prompt_max_tokens": "total_max_tokens",
}

# todo: namespaced default sets to allow for opt-in defaults upgrades
DEFAULTS = get_defaults(Config)
LEGACY_DEFAULTS = {**copy.deepcopy(DEFAULTS), **get_defaults(LegacyConfig)}


def overlay(base: dict | list, updates: dict | list, none_clears_array: bool = True):
    if isinstance(base, Secret):
        base = base.get_secret_value()
    if isinstance(updates, Secret):
        updates = updates.get_secret_value()
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
            if key == "em_overrides":
                result[key] = overlay(result[key], value, none_clears_array=False)
            else:
                result[key] = overlay(result[key], value, none_clears_array)
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
            result[key] = overlay(result[key], value, none_clears_array)
        else:
            if isinstance(result, list):
                # assume lists of primitives act like sets
                if value is None and none_clears_array:
                    # use null to mean unset
                    result.clear()
                else:
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


def get_union_members(union: typing.Union | types.UnionType | typing.Annotated):
    if typing.get_origin(union) == Annotated:
        return get_union_members(typing.get_args(union)[0])
    elif typing.get_origin(union) in (typing.Union, types.UnionType):
        return typing.get_args(union)
    else:
        raise TypeError("Unrecognized union type: " + str(type(union)))


EM_KEYS = set(EmConfig.model_fields.keys())
SHARED_INTERFACE_KEYS = set(SharedInterfaceConfig.model_fields.keys())
ALL_INTERFACE_KEYS = set()
for interface_config_subclass in get_union_members(InterfaceConfig):
    ALL_INTERFACE_KEYS.update(interface_config_subclass.model_fields.keys())


def transpose_keys(kv: dict, defaults: dict = DEFAULTS):
    result = {
        "em": {},
        "interfaces": kv.get("interfaces", defaults["interfaces"]),
    }
    for key in kv.copy():
        if key in EM_KEYS:
            result["em"][key] = kv[key]
            del kv[key]
        elif key in SHARED_INTERFACE_KEYS:
            for interface in result["interfaces"]:
                interface[key] = kv[key]
            del kv[key]
        elif key in ALL_INTERFACE_KEYS:
            for interface_config_subclass in get_union_members(InterfaceConfig):
                if key in interface_config_subclass.model_fields.keys():
                    for interface in result["interfaces"]:
                        if (
                            interface["name"]
                            == interface_config_subclass.model_fields["name"].default
                        ):
                            interface[key] = kv[key]
        else:
            result[key] = kv[key]
    return result


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
    dictionary = overlay(defaults, transpose_keys(rename_keys(kv, ALIASES), defaults))
    return Config(**dictionary)
