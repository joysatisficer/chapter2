import os
import time
import dataclasses
from typing import TypeVar, Iterable, Callable
from pathlib import Path

import yaml
import dhall
from aioitertools.more_itertools import take as async_take

import resolve_config
from intermodel import callgpt
from declarations import Message, UserID, MessageHistory, Author, Config
from message_formats import MessageFormat, MESSAGE_FORMAT_REGISTRY
from discord_interface import DiscordInterface, get_yaml_from_channel
from mufflers import repeats_prompt_sentence, has_http
from character_faculty import CharacterFaculty


async def generate_response(
    my_user_id: UserID, history: MessageHistory, config: Config
):
    message_format = MESSAGE_FORMAT_REGISTRY[config.message_format]
    author = Author(config.name, my_user_id)
    recent_messages = await async_take(config.recency_window, history)
    completion_prefix = message_format.name_prefix(config.name)
    message_history_ensemble = (
        ""
        if config.message_history_header is None
        else (config.message_history_header + "\n")
        + format_message_section(message_format, recent_messages)
        + completion_prefix
    )
    if "character" in config.enabled_faculties:
        character_faculty = CharacterFaculty(config)
        fetched = await character_faculty.fetch(history, config)

        def token_limit_not_reached(s):
            return len(callgpt.tokenize(config.continuation_model, s)) < (
                callgpt.max_token_length(config.continuation_model)
                - config.continuation_max_tokens
            )

        faculty_text = format_message_section(
            message_format,
            fetched,
            separator=config.scene_break,
            while_=token_limit_not_reached,
        )
    else:
        faculty_text = ""
    prompt = faculty_text + message_history_ensemble
    stop_sequences = unique(
        config.stop_sequences
        + [
            # if the completion prefix is an empty string,
            # it's possible for a stop sequence to be generated
            # immediately. if that's the case, we don't want to
            # prepend a newline to author-based stop sequences
            ("" if completion_prefix == "" else "\n")
            + message_format.name_prefix(message.author.name)
            for message in recent_messages
            if message.author.name != config.name
        ]
    )
    has_valid_reply = False
    tries = 0
    while not has_valid_reply and tries < 3:
        tries += 1
        replies = []
        has_valid_reply = True
        async for reply in get_replies(
            config, prompt, completion_prefix, config.name, author, stop_sequences
        ):
            muffler_results = {
                "repeats_prompt_sentence": repeats_prompt_sentence(
                    reply.content, prompt
                ),
                "has_http": has_http(reply.content, prompt),
            }
            if any(filter(lambda n: not n, muffler_results.keys())):
                has_valid_reply = False
                print("Muffled>>", reply, "<<Muffled", muffler_results, sep="")
            else:
                replies.append(reply)
    for reply in replies:
        yield reply


async def get_replies(
    config: Config,
    prompt: str,
    completion_prefix: str,
    my_name: str,
    author: Author,
    stop_sequences: list[str] = None,
):
    if config.prevent_scene_break:
        logit_bias = {
            callgpt.tokenize(config.continuation_model, config.scene_break.strip("\n"))[
                0
            ]: -100
        }
    else:
        logit_bias = {}
    print(logit_bias)
    completion = (
        await callgpt.complete(
            prompt=prompt,
            temperature=config.temperature,
            max_tokens=config.continuation_max_tokens,
            frequency_penalty=config.frequency_penalty,
            presence_penalty=config.presence_penalty,
            model=config.continuation_model,
            stop=stop_sequences[:3] if stop_sequences is not None else None,
            vendor_config=config.vendors,
            logit_bias=logit_bias,
        )
    )["completions"][0]["text"]
    print("Completion>>", completion.replace("\n", r"\n"), "<<Completion", sep="")
    # Todo: Client-side stop sequences
    for message in MESSAGE_FORMAT_REGISTRY[config.message_format].parse(
        completion_prefix + completion
    ):
        # accept messages from myself or without prefixes
        if message.author.name in (my_name, ""):
            yield dataclasses.replace(message, author=author)
        else:
            break


def format_message_section(
    message_format: MessageFormat,
    messages: list[Message],
    separator: str = "",
    while_: Callable[[str], bool] = lambda _: True,
) -> str:
    prompt = ""
    for message in messages[::-1]:
        new_prompt = prompt + separator + message_format.render(message)
        if not while_(new_prompt):
            break
        prompt = new_prompt
    return prompt


T = TypeVar("T")


def unique(iterable: Iterable[T]) -> list[T]:
    return list(dict.fromkeys(iterable))


def get_config_getter(bot_config: Config):
    async def get_config(channel: "discord.abc.MessageableChannel") -> Config:
        return resolve_config.load_config_from_kv(
            await get_yaml_from_channel(channel),
            bot_config.model_dump(),
        )

    return get_config


def run_em(name):
    parent_dir = Path(__file__).resolve().parents[1]
    em_folder = parent_dir / "ems" / name
    try:
        with open(em_folder / "config.yaml") as file:
            kv = yaml.safe_load(file)
            if kv is None:
                kv = {}
    except FileNotFoundError:
        # warning, loading dhall is not memory safe
        with open(em_folder / "config.dhall") as file:
            kv = dhall.load(file)
    with open(parent_dir / "ems/vendors.yaml") as file:
        kv = {**kv, **yaml.safe_load(file)}
    kv["em_folder"] = em_folder
    for subpath in em_folder.iterdir():
        if subpath.name in Config.model_fields.keys():
            kv[subpath.name] = subpath.read_text()
    if "name" not in kv:
        kv["name"] = name
    if kv.get("legacy", False):
        config = resolve_config.load_config_from_kv(kv, resolve_config.LEGACY_DEFAULTS)
        del kv["legacy"]
    else:
        config = resolve_config.load_config_from_kv(kv, resolve_config.DEFAULTS)
    interface = DiscordInterface(
        kv["name"], generate_response, get_config_getter(config)
    )
    interface.run(config.discord_token)


if __name__ == "__main__":
    from rich.traceback import install

    install(show_locals=True)
    import fire

    fire.Fire(run_em)
