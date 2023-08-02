import os
import time
from typing import TypeVar, Iterable

import aioitertools.more_itertools
import yaml

import resolve_config
from declarations import Message, UserID, MessageHistory, Author, Config
from message_formats import irc_message_format, MessageFormat
from discord_interface import DiscordInterface, get_yaml_from_channel
from mufflers import repeats_prompt_sentence, has_http
from intermodel import callgpt
from pathlib import Path


async def generate_response(
    my_user_id: UserID, history: MessageHistory, config: Config
):
    author = Author(my_user_id, config.name)
    recent_messages = await aioitertools.more_itertools.take(20, history)
    completion_prefix = irc_message_format.name_prefix(config.name)
    prompt = (
        format_message_section(irc_message_format, recent_messages) + completion_prefix
    )
    stop_sequences = unique(
        "\n" + irc_message_format.name_prefix(message.author.name)
        for message in recent_messages
        if message.author.name != config.name
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
                    reply.message, prompt
                ),
                "has_http": has_http(reply.message, prompt),
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
        )
    )["completions"][0]["text"]
    print("Completion>>", completion.replace("\n", r"\n"), "<<Completion", sep="")
    # TODO: Truncate last 512 tokens
    for name, message in irc_message_format.parse(completion_prefix + completion):
        # accept messages from myself or without prefixes
        if name == my_name or name == "":
            yield Message(author, message.strip())
        else:
            break


def format_message_section(
    message_format: MessageFormat, messages: list[Message]
) -> str:
    prompt = ""
    for message in messages[::-1]:
        prompt += message_format.wrap(message.author.name, message.message)
    return prompt + "\n"


T = TypeVar("T")


def unique(iterable: Iterable[T]) -> list[T]:
    return list(dict.fromkeys(iterable))


def get_config_getter(bot_config: Config):
    async def get_config(channel: "discord.abc.MessageableChannel") -> Config:
        return resolve_config.load_config_from_kv(
            await get_yaml_from_channel(channel),
            bot_config,
        )

    return get_config


def run_em(name):
    parent_dir = Path(__file__).resolve().parents[1]
    with open(parent_dir / f"people/{name}/config.yaml") as file:
        kv = yaml.safe_load(file)
        if kv is None:
            kv = {}
    with open(parent_dir / "people/vendors.yaml") as file:
        kv = {**kv, **yaml.safe_load(file)}
    with open(parent_dir / f"people/{name}/discord_token") as file:
        kv["discord_token"] = file.read().strip()
    if "name" not in kv:
        kv["name"] = name
    config = resolve_config.load_config_from_kv(kv)
    interface = DiscordInterface(name, generate_response, get_config_getter(config))
    interface.run(config.discord_token)


if __name__ == "__main__":
    from rich.traceback import install

    install(show_locals=True)
    import fire

    fire.Fire(run_em)
