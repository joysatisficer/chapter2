#!/usr/bin/env -S python -u
import asyncio
import dataclasses
from datetime import datetime
from typing import TypeVar, Iterable, Callable, AsyncIterable
from pathlib import Path

import yaml
import dhall
from aioitertools.more_itertools import take as async_take

import resolve_config
from intermodel import callgpt
from declarations import Message, UserID, MessageHistory, Author, Config
from message_formats import MessageFormat, MESSAGE_FORMAT_REGISTRY, web_document_format
from discord_interface import DiscordInterface, get_yaml_from_channel
from mufflers import repeats_prompt_sentence, has_http
from character_faculty import character_faculty
from metaphor_search_faculty import metaphor_search_faculty


async def generate_response(
    my_user_id: UserID, history: MessageHistory, config: Config
):
    message_format = MESSAGE_FORMAT_REGISTRY[config.message_format]
    author = Author(config.name, my_user_id)
    recent_messages = await async_take(config.recency_window, history)
    completion_prefix = message_format.name_prefix(config.name)
    # todo: config options for header and footer for every ensemble
    message_history_ensemble = (
        (
            ""
            if config.message_history_header is None
            else (config.message_history_header.format(now=datetime.now()) + "\n")
        )
        + format_message_section(message_format, recent_messages)
        + completion_prefix
    )
    if "metaphor-search" in config.enabled_faculties:
        web_results = await metaphor_search_faculty(history, config)
        # todo: make sure it's in the correct order
        metaphor_search_ensemble = format_message_section(
            web_document_format,
            web_results,
            # todo: configurable scene breaks for all faculties
            separator=config.scene_break,
            while_=has_not_reached_token_limit(
                config.continuation_model, config.metaphor_search_faculty_max_tokens
            ),
        )
    else:
        metaphor_search_ensemble = ""
    if "character" in config.enabled_faculties:
        # todo: token limits for all faculties
        fetched = await character_faculty(history, config)
        character_ensemble = format_message_section(
            message_format,
            fetched,
            separator=config.scene_break,
            while_=has_not_reached_token_limit(
                config.continuation_model,
                callgpt.max_token_length(config.continuation_model)
                - callgpt.count_tokens(
                    config.continuation_model, message_history_ensemble
                )
                - callgpt.count_tokens(
                    config.continuation_model, metaphor_search_ensemble
                )
                - config.continuation_max_tokens,
            ),
        )
    else:
        character_ensemble = ""
    # todo: make order of faculties configurable
    prompt = metaphor_search_ensemble + character_ensemble + message_history_ensemble
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
    print(prompt)
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


def has_not_reached_token_limit(continuation_model: str, token_limit: int):
    def token_limit_not_reached(s: str) -> bool:
        return len(callgpt.tokenize(continuation_model, s)) < token_limit

    return token_limit_not_reached


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


async def rehearse_em(config):
    """Rehearsal runs an em in mock mode when it awakens to populate caches"""

    async def mock_message_history_iterator():
        messages = [
            Message(Author("alice"), "hello"),
            Message(Author("bob"), "hi alice!"),
            Message(Author(config.name), "hi bob!"),
            Message(Author("alice"), f"hi {config.name}!"),
        ][::-1]
        for message in messages:
            yield message

    class MockMessageHistoryIterable(AsyncIterable):
        def __aiter__(self):
            return mock_message_history_iterator()

    async for response in generate_response(
        UserID(0, "rehearsal"), MockMessageHistoryIterable(), config
    ):
        pass


async def run_em(name):
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
        defaults = resolve_config.LEGACY_DEFAULTS
        del kv["legacy"]
    else:
        defaults = resolve_config.DEFAULTS
    config = resolve_config.load_config_from_kv(kv, defaults)
    interface = DiscordInterface(
        kv["name"], generate_response, get_config_getter(config)
    )
    import discord

    discord.utils.setup_logging()

    await asyncio.gather(
        asyncio.create_task(
            rehearse_em(
                resolve_config.load_config_from_kv(
                    kv, {**defaults, **resolve_config.REHEARSAL_CONFIG}
                )
            )
        ),
        # todo: set up logging
        asyncio.create_task(interface.start(config.discord_token)),
    )
    interface.run


if __name__ == "__main__":
    from rich.traceback import install

    install(show_locals=True)
    import fire

    fire.Fire(lambda name: asyncio.run(run_em(name)))
