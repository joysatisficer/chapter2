import dataclasses
from datetime import datetime
from functools import partial
from typing import TypeVar, Iterable

import asyncstdlib
from aioitertools.more_itertools import take as async_take
from intermodel import callgpt
from intermodel.callgpt import count_tokens, max_token_length

from declarations import UserID, ActionHistory, Author, Ensemble, Action
from faculties import FACULTY_NAME_TO_FUNCTION
from mufflers import repeats_prompt_sentence, has_http
from resolve_config import Config, LayerOfEnsembleFormat, EnsembleFormat


async def generate_response(my_user_id: UserID, history: ActionHistory, config: Config):
    count_continuation_model_tokens = partial(count_tokens, config.continuation_model)
    author = Author(config.name, my_user_id)
    recent_messages = await async_take(config.recency_window, history)
    completion_prefix = config.message_history_format.name_prefix(config.name)
    ctx_vars = {"now": datetime.now()}
    # todo: message history normal ensemble configs including max_tokens
    message_history_ensemble = (
        (config.message_history_header.format(**ctx_vars) + "\n")
        + await format_ensemble(
            recent_messages,
            # todo: move to resolve_config
            [
                LayerOfEnsembleFormat(
                    format=config.message_history_format,
                    max_items=config.recency_window,
                    separator="",
                    footer="",
                )
            ],
            config.continuation_model,
        )
        + completion_prefix
    )
    ensembles = []
    # TODO: Filter for empty ensembles
    for faculty_config in config.ensembles:
        faculty_results = FACULTY_NAME_TO_FUNCTION[faculty_config.faculty](
            history, faculty_config, config
        )
        local_max_tokens = min(
            (
                max_token_length(config.continuation_model)
                - sum(
                    [
                        count_continuation_model_tokens(ensemble)
                        for ensemble in ensembles + [message_history_ensemble]
                    ]
                )
                - config.continuation_max_tokens
            ),
            faculty_config.ensemble_format[0].max_tokens,
        )
        ensemble_format = [
            faculty_config.ensemble_format[0].model_copy(
                update=dict(max_tokens=local_max_tokens)
            )
        ] + faculty_config.ensemble_format[1:]
        ensemble = await format_ensemble(
            faculty_results, ensemble_format, config.continuation_model
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
                yield reply


async def get_replies(
    config: Config,
    prompt: str,
    completion_prefix: str,
    my_name: str,
    author: Author,
    stop_sequences: list[str] = None,
):
    logit_bias = {}
    for logit, bias in config.logit_bias.items():
        if isinstance(logit, int):
            logit_bias[logit] = bias
        elif isinstance(logit, str):
            tokens = callgpt.tokenize(config.continuation_model, logit)
            assert len(tokens) == 1, "logit_bias invalid string logit"
            logit_bias[tokens[0]] = bias
        else:
            raise NotImplementedError("Unrecognized logit_bias key type")

    if config.prevent_scene_break:
        scene_break_token = callgpt.tokenize(
            config.continuation_model, config.scene_break.strip("\n")
        )[0]
        logit_bias[scene_break_token] = -100
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
            best_of=config.best_of,
        )
    )["completions"][0]["text"]
    if callgpt.pick_vendor(config.continuation_model, config.vendors) == "fake-local":
        print(
            "Continues>>",
            "{",
            callgpt.count_tokens(config.continuation_model, completion),
            "tokens omitted}",
            "<<Continues",
            sep="",
        )
    else:
        print("Continues>>", completion.replace("\n", r"\n"), "<<Continues", sep="")
    # Todo: Client-side stop sequences
    for message in config.message_history_format.parse(completion_prefix + completion):
        # accept messages from myself or without prefixes
        if (
            config.prevent_gpt_topic_change
            and message.content.strip() == config.scene_break.strip()
        ):
            break
        elif message.author is None or message.author.name == my_name:
            yield dataclasses.replace(message, author=author)
        else:
            break


async def format_ensemble(
    ensemble: Ensemble,
    ensemble_format: EnsembleFormat,
    tokenization_model: str,
) -> str:
    prompt = None
    local_format = ensemble_format[0]
    async for i, subensemble in asyncstdlib.enumerate(ensemble):
        if i >= local_format.max_items:
            break
        if isinstance(subensemble, Action):
            string = local_format.format.render(subensemble)
        else:
            string = await format_ensemble(
                subensemble, ensemble_format[1:], tokenization_model
            )
        if prompt is None:
            new_prompt = local_format.header + string
        else:
            new_prompt = string + local_format.separator + prompt
        if (
            count_tokens(tokenization_model, new_prompt + local_format.footer)
            > local_format.max_tokens
        ):
            break
        else:
            prompt = new_prompt
    if prompt is None:
        # if ensemble has no members, don't include header or footer
        return ""
    else:
        return prompt + local_format.footer


T = TypeVar("T")


def unique(iterable: Iterable[T]) -> list[T]:
    return list(dict.fromkeys(iterable))
