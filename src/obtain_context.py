import os
from util import chatgpt
from typing import TypeVar, Iterable

import aioitertools.more_itertools
import tiktoken
from openai import ChatCompletion

from declarations import Message, UserID, ActionHistory, Author, JSON
from message_formats import irc_message_format, MessageFormat
from discord_interface import DiscordInterface, get_channel_metadata_from_topic_as_yaml
from mufflers import repeats_prompt_sentence, has_http


def as_a_language_model(reply, prompt):
    import re

    return (
        re.match(r"[Aa]s an? (AI|artificial intelligence) language model", reply)
        or "rlhf"
    )


async def generate_response(my_user_id: UserID, history: ActionHistory, metadata: JSON):
    if metadata.get("mystiqa") != True:
        return
    my_name = "allison"
    author = Author(my_name, my_user_id)
    recent_messages = await aioitertools.more_itertools.take(50, history)
    completion_prefix = irc_message_format.name_prefix(my_name)
    prompt = (
        format_message_section(irc_message_format, recent_messages) + completion_prefix
    )
    stop_sequences = unique(
        irc_message_format.name_prefix(message.author.name)
        for message in recent_messages
    )
    active_mufflers = [repeats_prompt_sentence, has_http]
    has_valid_reply = False
    tries = 0
    while not has_valid_reply and tries < 3:
        tries += 1
        replies = []
        has_valid_reply = True
        async for reply in get_replies(
            metadata,
            recent_messages,
            completion_prefix,
            my_name,
            author,
            stop_sequences,
        ):
            if any((muffler(reply.message, prompt)) for muffler in active_mufflers):
                has_valid_reply = False
                print("Muffled", reply)
            else:
                replies.append(reply)
    for reply in replies:
        yield reply


async def get_replies(
    metadata: JSON,
    recent_messages: list[Message],
    completion_prefix: str,
    my_name: str,
    author: Author,
    stop_sequences: list[str] = None,
):
    model = metadata.get("engines.complete", "gpt-3.5-turbo")
    discouraged = [
        " language",
        " Language",
        "-based",
        " convers",
        " text",
        " based",
        " digital",
        " virtual",
        " entity",
        " A",
        "-powered",
        " online",
        " software",
        " program",
    ]
    banned = [
        " AI",
        " artificial",
        "AI",
        " Artificial",
        "Art",
        " Assistant",
        " ai",
        "ai",
        " Ai",
        " model",
        " Model",
        "-generation",
        ".I",
        " assistant",
        " Al",
        " chat",
        "_AI",
        ".ai",
        " computer",
        " artificially",
    ]
    logit_bias = {
        **weigh_tokens(model, banned),
        **weigh_tokens(model, discouraged, amount=-20),
    }
    completion = ChatCompletion.create(
        temperature=metadata.get("temperature", 1.25),
        max_tokens=200,
        messages=[
            {
                "role": "system",
                "content": "Ask the user questions to obtain context. Be curious.",
            },
            *[
                {
                    "role": "assistant"
                    if message.author.user_id == author.user_id
                    else "user",
                    "content": message.message,
                    "name": chatgpt.strip_name(
                        message.author.name.replace("Mystiqa", "allison").translate(
                            str.maketrans(
                                {
                                    "&": "amp",
                                    ".": "dot",
                                }
                            )
                        )
                    ),
                }
                for message in recent_messages[::-1]
            ],
            #            {
            #                "role": "assistant",
            #                "content": "Thanks for sharing. Can you tell me a little more about what's been going on?"
            #            }
        ],
        frequency_penalty=0.3,
        presence_penalty=1.0,
        model=model,
        stop=stop_sequences[:3] if stop_sequences is not None else None,
        # logit_bias=logit_bias,
    )["choices"]
    print(list(i["message"]["content"] for i in completion))
    yield Message(author, completion[0]["message"]["content"].strip())


def weigh_tokens(model, tokens, amount=-100):
    encoder = tiktoken.encoding_for_model(model)
    bias = {}
    for token in tokens:
        bias[encoder.encode_single_token(token)] = amount
    return bias


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
        "allison", generate_response, get_channel_metadata_from_topic_as_yaml
    )
    interface.run(os.environ["DISCORD_TOKEN"])
