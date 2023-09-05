import time
from typing import Callable

from asgiref.sync import sync_to_async
from aioitertools.more_itertools import take as async_take

import dateutil.parser
from metaphor_python import Metaphor
from intermodel import callgpt

from declarations import MessageHistory, Message, Author
from message_formats import MessageFormat, irc_message_format
from resolve_config import Config


async def metaphor_search_faculty(
    history: MessageHistory, config: Config
) -> list[Message]:
    message_history_string = format_message_section(
        irc_message_format,
        await async_take(
            config.metaphor_search_faculty_recent_message_attention, history
        ),
    )
    metaphor_client = Metaphor(config.metaphor_search_api_key)
    results = (
        await sync_to_async(metaphor_client.search)(
            trim_tokens("gpt2", message_history_string, 1000),
            num_results=30,
        )
    ).results
    document_ids = []
    for item in results:
        document_ids.append(item.id)
    document_contents = (
        await sync_to_async(metaphor_client.get_contents)(document_ids)
    ).contents
    returned_messages = []
    for document_content, result in zip(document_contents, results):
        if result.published_date is None:
            published_timestamp = None
        else:
            published_timestamp = time.mktime(
                dateutil.parser.parse(result.published_date).timetuple()
            )
        returned_messages.append(
            Message(
                Author(document_content.url),
                document_content.extract,
                timestamp=published_timestamp,
            )
        )
    return returned_messages


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


def trim_tokens(model: str, string: str, n_tokens: int):
    used_tokens = callgpt.tokenize(model, string)[-n_tokens:]
    return callgpt.untokenize(model, used_tokens)
