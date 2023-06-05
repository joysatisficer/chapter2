import os
import time
from typing import TypeVar, Iterable

import aioitertools.more_itertools
from openai import Completion

from declarations import Message, UserID, MessageHistory, Author, JSON
from message_formats import irc_message_format, MessageFormat
from discord_interface import DiscordInterface, get_channel_metadata_from_topic_as_yaml
from mufflers import repeats_prompt_sentence, has_http
from intermodel import callgpt


async def generate_response(
    my_user_id: UserID, history: MessageHistory, metadata: JSON
):
    my_name = "sercy"
    author = Author(my_user_id, my_name)
    recent_messages = await aioitertools.more_itertools.take(20, history)
    completion_prefix = irc_message_format.name_prefix(my_name)
    prompt = (
        format_message_section(irc_message_format, recent_messages) + completion_prefix
    )
    stop_sequences = unique(
        "\n" + irc_message_format.name_prefix(message.author.name)
        for message in recent_messages
        if message.author.name != my_name
    )
    active_mufflers = [repeats_prompt_sentence, has_http]
    has_valid_reply = False
    tries = 0
    while not has_valid_reply and tries < 3:
        tries += 1
        replies = []
        has_valid_reply = True
        async for reply in get_replies(
            prompt, completion_prefix, my_name, author, stop_sequences
        ):
            if any((muffler(reply.message, prompt)) for muffler in active_mufflers):
                has_valid_reply = False
                print("Muffled", reply)
            else:
                replies.append(reply)
    for reply in replies:
        yield reply


async def get_replies(
    prompt: str,
    completion_prefix: str,
    my_name: str,
    author: Author,
    stop_sequences: list[str] = None,
):
    completion = (
        await callgpt.complete(
            prompt=prompt,
            temperature=1.0,
            max_tokens=50,
            frequency_penalty=0.3,
            presence_penalty=1.5,
            model="davinci:ft-academics-illinois-2022-12-26-04-18-37",
            stop=stop_sequences[:3] if stop_sequences is not None else None,
        )
    )["completions"][0]["text"]
    print("Completion>>", completion, "<<Completion")
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


if __name__ == "__main__":
    interface = DiscordInterface(
        "sercy", generate_response, get_channel_metadata_from_topic_as_yaml
    )
    interface.run(os.environ["DISCORD_TOKEN"])
