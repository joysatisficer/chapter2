import re
import time
from typing import Callable

from asgiref.sync import sync_to_async
from aioitertools.more_itertools import take as async_take

import arrow
import dateutil.parser
from functools import cache
from exa_py import Exa
from intermodel import callgpt

from declarations import ActionHistory, Message, Author
from message_formats import MessageFormat, IRCMessageFormat
from ontology import (
    ExaSearchFacultyConfig,
    ExaSearchFullTextConfig,
    ExaSearchHighlightsConfig,
    EmConfig,
)
from trace import trace

SharedExa = cache(Exa)


@trace
async def exa_search_faculty(
    history: ActionHistory, faculty_config: ExaSearchFacultyConfig, em: EmConfig
):
    message_history_string = format_message_section(
        faculty_config.input_format,
        await async_take(faculty_config.recent_message_attention, history),
    ) + faculty_config.input_format.name_prefix(em.name)
    api_key = em.exa_search_api_key.get_secret_value()
    if api_key == "sk-rehearsal":
        return
    else:
        exa_client = SharedExa(em.exa_search_api_key.get_secret_value())
    kwparams = {
        "use_autoprompt": faculty_config.use_autoprompt,
        "include_domains": faculty_config.include_domains,
        "exclude_domains": faculty_config.exclude_domains,
    }
    if isinstance(faculty_config.output, ExaSearchFullTextConfig):
        kwparams["text"] = {
            "max_characters": faculty_config.output.max_characters,
            "include_html_tags": faculty_config.output.include_html_tags,
        }
    else:
        kwparams["highlights"] = {
            "highlights_per_url": faculty_config.output.highlights_per_url,
            "num_sentences": faculty_config.output.sentences_per_highlight,
        }
    to_iso8601 = lambda date_string: (
        None
        if date_string is None
        else arrow.utcnow().dehumanize(date_string).isoformat()
    )
    kwparams["start_crawl_date"] = to_iso8601(faculty_config.start_crawl_date)
    kwparams["end_crawl_date"] = to_iso8601(faculty_config.end_crawl_date)
    kwparams["start_published_date"] = to_iso8601(faculty_config.start_published_date)
    kwparams["end_published_date"] = to_iso8601(faculty_config.end_published_date)
    yielded_urls = set()
    n_results = faculty_config.impl_hint_initial_num_results
    while True:
        kwparams["num_results"] = n_results
        results = sorted(
            (
                await sync_to_async(
                    exa_client.search_and_contents, thread_sensitive=False
                )(
                    trim_tokens("gpt2", message_history_string, 1000),
                    **kwparams,
                )
            ).results,
            key=lambda item: item.score,
            reverse=True,
        )
        if len(results) == 0:
            return
        for result in results:
            if result.url in yielded_urls or result.url in faculty_config.ignored_urls:
                continue
            if result.published_date is None:
                published_timestamp = None
            else:
                published_timestamp = time.mktime(
                    dateutil.parser.parse(result.published_date).timetuple()
                )
            if isinstance(faculty_config.output, ExaSearchFullTextConfig):
                text = result.text
            else:
                # todo: convert highlights into nested ensemble
                text = "\n".join(result.highlights)
            cleaned_text = re.sub(
                r"\n{3,}", "\n\n", strip_leading_indentation(text)
            ).strip()
            yield Message(
                Author(result.url), cleaned_text, timestamp=published_timestamp
            )
            yielded_urls.add(result.url)
        if n_results == faculty_config.max_results:
            break
        if n_results < faculty_config.max_results:
            n_results = min(
                n_results + faculty_config.impl_hint_initial_num_results,
                faculty_config.max_results,
            )


def strip_leading_indentation(string: str) -> str:
    result = []
    for line in string.splitlines():
        result.append(line.lstrip())
    return "\n".join(result)


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
