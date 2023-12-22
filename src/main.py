#!/usr/bin/env -S python -u
import os
import signal
import sys
import asyncio
import dataclasses
from datetime import datetime
from typing import TypeVar, Iterable, Callable, AsyncIterable
from pathlib import Path
from functools import partial

import yaml
from aioitertools.more_itertools import take as async_take

import resolve_config
from resolve_config import Config
from intermodel import callgpt
from intermodel.callgpt import count_tokens, max_token_length
from declarations import Message, UserID, ActionHistory, Author
from message_formats import MessageFormat
from interfaces.discord_interface import DiscordInterface, get_yaml_from_channel
from interfaces.addons.discord_generate_avatar import discord_generate_avatar
from interfaces.chatcompletions_interface import ChatCompletionsInterface
from interfaces.completions_interface import CompletionsInterface
from mufflers import repeats_prompt_sentence, has_http
from faculties.character_faculty import character_faculty
from faculties.metaphor_search_faculty import metaphor_search_faculty


# move these to __init__.py
FACULTY_NAME_TO_FUNCTION = {
    "character": character_faculty,
    "metaphor_search": metaphor_search_faculty,
}

INTERFACE_NAME_TO_INTERFACE = {
    "discord": DiscordInterface,
    "completions": CompletionsInterface,
    "chatcompletions": ChatCompletionsInterface,
}

INTERFACE_ADDON_NAME_TO_ADDON = {
    "discord": {
        "generate_avatar": discord_generate_avatar,
    },
}


async def generate_response(my_user_id: UserID, history: ActionHistory, config: Config):
    count_continuation_model_tokens = partial(count_tokens, config.continuation_model)
    author = Author(config.name, my_user_id)
    recent_messages = await async_take(config.recency_window, history)
    completion_prefix = config.message_history_format.name_prefix(config.name)
    ctx_vars = {"now": datetime.now()}
    message_history_ensemble = (
        (config.message_history_header.format(**ctx_vars) + "\n")
        + await format_message_section(config.message_history_format, recent_messages)
        + completion_prefix
    )
    ensembles = []
    for faculty_config in config.ensembles:
        faculty_results = FACULTY_NAME_TO_FUNCTION[faculty_config.faculty](
            history, faculty_config, config
        )
        ensemble = (
            faculty_config.header.format(**ctx_vars)
            + await format_message_section(
                faculty_config.format,
                faculty_results,
                separator=faculty_config.separator,
                while_=has_not_reached_token_limit(
                    config.continuation_model,
                    min(
                        (
                            max_token_length(config.continuation_model)
                            - sum(
                                [
                                    count_continuation_model_tokens(ensemble)
                                    for ensemble in ensembles
                                    + [message_history_ensemble]
                                ]
                            )
                            - config.continuation_max_tokens
                            - count_continuation_model_tokens(
                                faculty_config.header.format(**ctx_vars)
                            )
                            - count_continuation_model_tokens(
                                faculty_config.footer.format(**ctx_vars)
                            )
                        ),
                        faculty_config.max_tokens,
                    ),
                ),
            )
            + faculty_config.footer.format(**ctx_vars)
        )
        ensembles.append(ensemble)
    prompt = "".join(ensembles + [message_history_ensemble])
    assert count_continuation_model_tokens(
        prompt
    ) + config.continuation_max_tokens < max_token_length(config.continuation_model)
    stop_sequences = unique(
        config.stop_sequences
        + [
            # if the completion prefix is an empty string,
            # it's possible for a stop sequence to be generated
            # immediately. if that's the case, we don't want to
            # prepend a newline to author-based stop sequences
            ("" if completion_prefix == "" else "\n")
            + config.message_history_format.name_prefix(message.author.name)
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
    for message in config.message_history_format.parse(completion_prefix + completion):
        # accept messages from myself or without prefixes
        if message.author is None or message.author.name == my_name:
            yield dataclasses.replace(message, author=author)
        else:
            break


def has_not_reached_token_limit(continuation_model: str, token_limit: int):
    def token_limit_not_reached(s: str) -> bool:
        return len(callgpt.tokenize(continuation_model, s)) < token_limit

    return token_limit_not_reached


async def format_message_section(
    message_format: MessageFormat,
    messages: AsyncIterable[Message] | Iterable[Message],
    separator: str = "",
    while_: Callable[[str], bool] = lambda _: True,
) -> str:
    prompt = ""
    if hasattr(messages, "__aiter__"):
        async for message in messages:
            if prompt == "":
                new_prompt = message_format.render(message)
            else:
                new_prompt = message_format.render(message) + separator + prompt
            if not while_(new_prompt):
                break
            prompt = new_prompt
        return prompt
    else:
        for message in messages:
            if prompt == "":
                new_prompt = message_format.render(message)
            else:
                new_prompt = message_format.render(message) + separator + prompt
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
            await get_yaml_from_channel(channel) if channel is not None else {},
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
    with open(em_folder / "config.yaml") as file:
        kv = yaml.safe_load(file)
        if kv is None:
            kv = {}
    try:
        with open(os.path.expanduser("~/.config/chapter2/vendors.yaml")) as file:
            kv = {**kv, **yaml.safe_load(file)}
    except FileNotFoundError:
        pass
    try:
        with open(parent_dir / "ems/vendors.yaml") as file:
            kv = {**kv, **yaml.safe_load(file)}
    except FileNotFoundError:
        pass
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
    args = get_config_getter(config), generate_response, kv["name"]
    interfaces = []
    for interface in config.interfaces:
        interface_name = interface.name
        addons = []
        if hasattr(interface, "addons"):
            for addon in interface.addons:
                addons.append(
                    INTERFACE_ADDON_NAME_TO_ADDON[interface_name][addon.name](addon)
                )
        base_interface = INTERFACE_NAME_TO_INTERFACE[interface_name]
        if len(addons) == 0:
            interfaces.append((base_interface, interface))
        else:
            interfaces.append(
                (
                    type(
                        "Custom" + base_interface.__name__,
                        (*addons, base_interface),
                        {},
                    ),
                    interface,
                )
            )

    interface_instances = []
    for interface, interface_config in interfaces:
        interface_instances.append(interface(*args, interface_config))

    def handle_interrupt(sig, frame):
        for interface_instance in interface_instances:
            interface_instance.stop(sig, frame)

    signal.signal(signal.SIGINT, handle_interrupt)

    await asyncio.gather(
        asyncio.create_task(
            rehearse_em(
                resolve_config.load_config_from_kv(
                    kv, {**defaults, **resolve_config.REHEARSAL_CONFIG}
                )
            )
        ),
        *[interface_instance.start() for interface_instance in interface_instances],
    )


if __name__ == "__main__":
    from rich.traceback import install
    import fire
    import selectors

    install(show_locals=not sys.__stdin__.isatty(), suppress=[asyncio, fire, selectors])

    fire.Fire(lambda name: asyncio.run(run_em(name)))
