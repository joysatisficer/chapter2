import platform
import dataclasses
from datetime import datetime
from functools import partial
from typing import TypeVar, Iterable

import asyncstdlib
from aioitertools.more_itertools import take as async_take
from intermodel import callgpt
from intermodel.callgpt import count_tokens, max_token_length

from declarations import ActionHistory, Author, Ensemble, Action, Message
from faculties import FACULTY_NAME_TO_FUNCTION
from mufflers import mufflers
from ontology import LayerOfEnsembleFormat, EnsembleFormat, EmConfig
from trace import trace, log_trace_id_to_console


@trace
async def get_prompt(em: EmConfig, history: ActionHistory):
    count_continuation_model_tokens = partial(count_tokens, em.continuation_model)
    completion_prefix = (
        em.message_history_format.name_prefix(em.name) if em.name_prefix else ""
    )
    ctx_vars = {"now": datetime.now(), "hostname": platform.node()}
    max_prompt_length = (
        min(max_token_length(em.continuation_model), em.total_max_tokens)
        - em.continuation_max_tokens
    )
    message_history_ensemble = (
        (em.message_history_header.format(**ctx_vars))
        + await format_ensemble(
            history,
            # todo: move to ontology
            [
                LayerOfEnsembleFormat(
                    format=em.message_history_format,
                    max_items=em.recency_window,
                    max_tokens=min(max_prompt_length, em.message_history_max_tokens),
                    operator=em.message_history_operator,
                    separator=em.message_history_separator,
                    footer=em.message_history_footer,
                )
            ],
            em.continuation_model,
            ctx_vars,
        )
        + completion_prefix
    )
    ensembles = []
    # TODO: Filter for empty ensembles
    for faculty_config in em.ensembles:
        faculty_results = FACULTY_NAME_TO_FUNCTION[faculty_config.faculty](
            em, faculty_config, history
        )
        local_max_tokens = min(
            (
                max_prompt_length
                - sum(
                    [
                        count_continuation_model_tokens(ensemble)
                        for ensemble in ensembles + [message_history_ensemble]
                    ]
                )
            ),
            faculty_config.ensemble_format[0].max_tokens,
        )
        if faculty_config.faculty == "history":
            ensemble_format = [
                faculty_config.ensemble_format[0].model_copy(
                    update=dict(
                        max_tokens=local_max_tokens,
                        format=em.message_history_format,
                        separator=em.message_history_separator,
                        operator=em.message_history_operator,
                    )
                )
            ]
        else:
            ensemble_format = [
                faculty_config.ensemble_format[0].model_copy(
                    update=dict(max_tokens=local_max_tokens)
                )
            ] + faculty_config.ensemble_format[1:]
        ensemble = await format_ensemble(
            faculty_results, ensemble_format, em.continuation_model, ctx_vars
        )
        ensembles.append(ensemble)
    prompt = "".join(ensembles + [message_history_ensemble])
    return prompt


@trace
async def generate_response(em: EmConfig, history: ActionHistory):
    author = Author(em.name)
    prompt = await get_prompt(em, history)
    completion_prefix = (
        em.message_history_format.name_prefix(em.name) if em.name_prefix else ""
    )
    recent_messages = await async_take(em.recency_window, history)
    stop_name_template = (
        em.message_history_format.name_format.format
        if em.message_history_format.name == "chat"
        else em.message_history_format.name_prefix
    )
    stop_sequences = unique(
        em.stop_sequences
        + [
            # if the completion prefix is an empty string,
            # it's possible for a stop sequence to be generated
            # immediately. if that's the case, we don't want to
            # prepend a newline to author-based stop sequences
            ("" if completion_prefix == "" else "\n")
            + stop_name_template(message.author.name)
            for message in recent_messages
            if (message.author is not None and message.author.name != em.name)
        ]
    )
    retry = True
    tries = 0
    while retry and tries < 3:
        tries += 1
        retry = False

        async for reply in get_replies(
            em, prompt, completion_prefix, em.name, author, stop_sequences
        ):
            for muffler in em.mufflers:
                if mufflers[muffler](prompt, reply.content):
                    retry = True
                    print("Muffled>>", muffler, "<<Muffled", sep="", flush=True)
                    break
            else:
                yield reply


@trace
async def get_replies(
    em: EmConfig,
    prompt: str,
    completion_prefix: str,
    my_name: str,
    author: Author,
    stop_sequences: list[str] = None,
):
    logit_bias = {}
    for logit, bias in em.logit_bias.items():
        if isinstance(logit, int):
            logit_bias[logit] = bias
        elif isinstance(logit, str):
            tokens = callgpt.tokenize(em.continuation_model, logit)
            assert len(tokens) == 1, f"logit_bias invalid string {logit}"
            logit_bias[tokens[0]] = bias
        else:
            raise NotImplementedError("Unrecognized logit_bias key type")

    if em.prevent_scene_break:
        scene_break_token = callgpt.tokenize(
            em.continuation_model, em.scene_break.strip("\n")
        )[0]
        logit_bias[scene_break_token] = -100
    else:
        logit_bias = {}
    print(prompt, flush=True)
    if em.continuation_model_local_tokenization:
        prompt = callgpt.tokenize(em.continuation_model, prompt)
    kwargs = {
        "message_history_format": em.message_history_format,
        "continuation_options": em.continuation_options,
        "name": em.name,
    }
    response = await callgpt.complete(
        prompt=trace.prompt(prompt, attr=True),
        temperature=em.temperature,
        max_tokens=em.continuation_max_tokens,
        frequency_penalty=em.frequency_penalty,
        presence_penalty=em.presence_penalty,
        model=em.continuation_model,
        stop=stop_sequences[:3] if stop_sequences is not None else None,
        vendor_config=em.vendors.get_secret_value(),
        logit_bias=logit_bias,
        best_of=em.best_of,
        **kwargs,
    )
    completion = response["completions"][0]["text"]
    reasoning_content = response["completions"][0].get("reasoning_content", None)
    if completion is None and reasoning_content is not None:
        if len(reasoning_content.split("</think>")) > 1:
            completion = "</think>".join(reasoning_content.split("</think>")[1:])
            reasoning_content = reasoning_content.split("</think>")[0]

    if reasoning_content is not None:
        print(
            "Reasoning>>",
            completion,
            # completion.replace("\n", r"\n"),
            "<<Reasoning",
            sep="",
            flush=True,
        )
    if (
        callgpt.pick_vendor(em.continuation_model, em.vendors.get_secret_value())
        == "fake-local"
    ):
        print(
            "Continues>>",
            "{",
            callgpt.count_tokens(em.continuation_model, completion),
            "tokens omitted}",
            "<<Continues",
            sep="",
            flush=True,
        )
    else:
        trace.continuation(completion, attr=True)
        print(
            "Continues>>",
            completion,
            # completion.replace("\n", r"\n"),
            "<<Continues",
            sep="",
            flush=True,
        )

    log_trace_id_to_console()
    # Todo: Client-side stop sequences

    if reasoning_content is not None:
        reasoning_message = Message(Author("reasoning_content"), reasoning_content)
        yield reasoning_message
    if completion:
        messages = []
        for message in em.message_history_format.parse(completion_prefix + completion):
            # accept messages from myself or without prefixes
            if (
                em.prevent_gpt_topic_change
                and message.content.strip() == em.scene_break.strip()
            ):
                break
            elif em.name_prefix_optional or (
                message.author is None or message.author.name == my_name
            ):
                if em.split_message:
                    yield dataclasses.replace(message, author=author)
                else:
                    messages.append(message)
            else:
                break
        if not em.split_message:
            for message in em.message_history_format.merge(messages):
                yield dataclasses.replace(message, author=author)


async def format_ensemble(
    ensemble: Ensemble,
    ensemble_format: EnsembleFormat,
    tokenization_model: str,
    ctx_vars: dict,
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
                subensemble, ensemble_format[1:], tokenization_model, ctx_vars
            )
        if prompt is None:
            new_prompt = string
        else:
            if local_format.operator == "prepend":
                new_prompt = string + local_format.separator + prompt
            else:
                new_prompt = prompt + local_format.separator + string
        if (
            count_tokens(
                tokenization_model,
                local_format.header.format(**ctx_vars)
                + new_prompt
                + local_format.footer.format(**ctx_vars),
            )
            > local_format.max_tokens
        ):
            break
        else:
            prompt = new_prompt
    if prompt is None:
        # if ensemble has no members, don't include header or footer
        return ""
    else:
        return (
            local_format.header.format(**ctx_vars)
            + prompt
            + local_format.footer.format(**ctx_vars)
        )


T = TypeVar("T")


def unique(iterable: Iterable[T]) -> list[T]:
    return list(dict.fromkeys(iterable))
