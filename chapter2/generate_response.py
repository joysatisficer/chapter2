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
from ontology import LayerOfEnsembleFormat, EnsembleFormat, EmConfig


async def generate_response(my_user_id: UserID, history: ActionHistory, em: EmConfig):
    count_continuation_model_tokens = partial(count_tokens, em.continuation_model)
    author = Author(em.name, my_user_id)
    recent_messages = await async_take(em.recency_window, history)
    completion_prefix = (
        em.message_history_format.name_prefix(em.name) if em.name_prefix else ""
    )
    ctx_vars = {"now": datetime.now()}
    # todo: message history normal ensemble configs including max_tokens
    message_history_ensemble = (
        (em.message_history_header.format(**ctx_vars) + "\n")
        + await format_ensemble(
            recent_messages,
            # todo: move to ontology
            [
                LayerOfEnsembleFormat(
                    format=em.message_history_format,
                    max_items=em.recency_window,
                    operator="prepend",
                    separator=em.message_history_separator,
                    footer=em.message_history_footer,
                )
            ],
            em.continuation_model,
        )
        + completion_prefix
    )
    max_prompt_length = min(
        max_token_length(em.continuation_model), em.prompt_max_tokens
    )
    ensembles = []
    # TODO: Filter for empty ensembles
    for faculty_config in em.ensembles:
        faculty_results = FACULTY_NAME_TO_FUNCTION[faculty_config.faculty](
            history, faculty_config, em
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
                - em.continuation_max_tokens
            ),
            faculty_config.ensemble_format[0].max_tokens,
        )
        ensemble_format = [
            faculty_config.ensemble_format[0].model_copy(
                update=dict(max_tokens=local_max_tokens)
            )
        ] + faculty_config.ensemble_format[1:]
        ensemble = await format_ensemble(
            faculty_results, ensemble_format, em.continuation_model
        )
        ensembles.append(ensemble)
    prompt = "".join(ensembles + [message_history_ensemble])
    assert (
        count_continuation_model_tokens(prompt) + em.continuation_max_tokens
        < max_prompt_length
    )
    stop_sequences = unique(
        em.stop_sequences
        + [
            # if the completion prefix is an empty string,
            # it's possible for a stop sequence to be generated
            # immediately. if that's the case, we don't want to
            # prepend a newline to author-based stop sequences
            ("" if completion_prefix == "" else "\n")
            + em.message_history_format.name_prefix(message.author.name)
            for message in recent_messages
            if message.author.name != em.name
        ]
    )
    has_valid_reply = False
    tries = 0
    while not has_valid_reply and tries < 3:
        tries += 1
        has_valid_reply = True
        async for reply in get_replies(
            em, prompt, completion_prefix, em.name, author, stop_sequences
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
    print(prompt)
    if em.continuation_model_local_tokenization:
        prompt = callgpt.tokenize(em.continuation_model, prompt)
    completion = (
        await callgpt.complete(
            prompt=prompt,
            temperature=em.temperature,
            max_tokens=em.continuation_max_tokens,
            frequency_penalty=em.frequency_penalty,
            presence_penalty=em.presence_penalty,
            model=em.continuation_model,
            stop=stop_sequences[:3] if stop_sequences is not None else None,
            vendor_config=em.vendors,
            logit_bias=logit_bias,
            best_of=em.best_of,
            **em.continuation_options,
        )
    )["completions"][0]["text"]
    if callgpt.pick_vendor(em.continuation_model, em.vendors) == "fake-local":
        print(
            "Continues>>",
            "{",
            callgpt.count_tokens(em.continuation_model, completion),
            "tokens omitted}",
            "<<Continues",
            sep="",
        )
    else:
        print("Continues>>", completion.replace("\n", r"\n"), "<<Continues", sep="")
    # Todo: Client-side stop sequences
    for message in em.message_history_format.parse(completion_prefix + completion):
        # accept messages from myself or without prefixes
        if (
            em.prevent_gpt_topic_change
            and message.content.strip() == em.scene_break.strip()
        ):
            break
        elif em.name_prefix_optional and (
            message.author is None or message.author.name == my_name
        ):
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
            new_prompt = string
        else:
            if local_format.operator == "prepend":
                new_prompt = string + local_format.separator + prompt
            else:
                new_prompt = prompt + local_format.separator + string
        if (
            count_tokens(
                tokenization_model,
                local_format.header + new_prompt + local_format.footer,
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
        return local_format.header + prompt + local_format.footer


T = TypeVar("T")


def unique(iterable: Iterable[T]) -> list[T]:
    return list(dict.fromkeys(iterable))
