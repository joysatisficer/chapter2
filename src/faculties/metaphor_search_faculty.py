import time
from typing import Callable

from asgiref.sync import sync_to_async
from aioitertools.more_itertools import take as async_take

import dateutil.parser
from functools import cache
from metaphor_python import AsyncMetaphor
from intermodel import callgpt

from declarations import ActionHistory, Message, Author
from message_formats import MessageFormat, irc_message_format
from resolve_config import Config, MetaphorSearchFacultyConfig

SharedAsyncMetaphor = cache(AsyncMetaphor)


async def metaphor_search_faculty(
    history: ActionHistory, faculty_config: MetaphorSearchFacultyConfig, config: Config
):
    message_history_string = format_message_section(
        irc_message_format,
        await async_take(faculty_config.recent_message_attention, history)
        + [Message(Author(config.name), "")],
    )
    metaphor_client = SharedAsyncMetaphor(config.metaphor_search_api_key)
    assert (
        faculty_config.max_tokens <= 11_000
    )  # max number of tokens it can return ais 10 documents * 1000 tokens per document by default
    results = sorted(
        (
            await metaphor_client.search(
                trim_tokens("gpt2", message_history_string, 1000),
                num_results=10,
                use_autoprompt=False,
                include_domains=faculty_config.include_domains,
                exclude_domains=faculty_config.exclude_domains,
                # start_crawl_date=faculty_config.start_crawl_date,
                # end_crawl_date=faculty_config.end_crawl_date,
                # start_published_date=faculty_config.start
            )
        ).results,
        key=lambda item: item.score,
        reverse=True,
    )
    if len(results) == 0:
        return
    document_contents_response = await metaphor_client.get_contents(
        [result.id for result in results]
    )
    document_contents = document_contents_response.contents
    for i, result in enumerate(results):
        if result.published_date is None:
            published_timestamp = None
        else:
            published_timestamp = time.mktime(
                dateutil.parser.parse(result.published_date).timetuple()
            )
        yield Message(
            Author(document_contents[i].url),
            document_contents[i].extract,
            timestamp=published_timestamp,
        )


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
