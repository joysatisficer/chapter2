import platform
import dataclasses
from datetime import datetime
from functools import partial
from typing import TypeVar, Iterable

import asyncstdlib
import nltk
from aioitertools.more_itertools import take as async_take
from intermodel import callgpt
from intermodel.callgpt import count_tokens, max_token_length

from declarations import ActionHistory, Author, Ensemble, Action, Message
from faculties import FACULTY_NAME_TO_FUNCTION
from mufflers import mufflers, divide_sentences
from ontology import (
    LayerOfEnsembleFormat,
    EnsembleFormat,
    EmConfig,
    FacultyConfig,
    MotifConfig,
    MessageHistoryEnsembleConfig,
    AbstractFacultyConfig,
)
from trace import trace, log_trace_id_to_console


# todo: this setup breaks tracing, fix
@trace
async def prompt_from(em: EmConfig, history: ActionHistory):
    count_continuation_model_tokens = partial(count_tokens, em.continuation_model)
    continuation_prefix = (
        em.message_history_format.name_prefix(em.name) if em.name_prefix else ""
    )
    ctx_vars = {"now": datetime.now(), "hostname": platform.node()}
    max_prompt_length = (
        min(max_token_length(em.continuation_model), em.total_max_tokens)
        - em.continuation_max_tokens
    )
    message_history_ensemble = (em.message_history_header.format(**ctx_vars)) + (
        await format_ensemble(
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
    )[1]
    # todo: make prompt_from and evaluate_ensembles lazy/streaming to allow
    # streaming prompt UI
    include_in_responses, ensembles = await evaluate_ensembles(
        count_continuation_model_tokens,
        ctx_vars,
        em,
        em.ensembles,
        history,
        max_prompt_length,
        message_history_ensemble,
    )
    prompt = "".join(ensembles) + continuation_prefix
    return include_in_responses, prompt


async def evaluate_ensembles(
    count_continuation_model_tokens,
    ctx_vars,
    em,
    ensembles_config,
    history,
    max_prompt_length,
    message_history_ensemble,
) -> tuple[list[Message], list[str]]:
    ensembles: list[str] = []
    include_in_responses: list[Message] = []
    # TODO: Filter for empty ensembles
    for ensemble_config in ensembles_config:
        if isinstance(ensemble_config, AbstractFacultyConfig):
            if ensemble_config.input_ensembles:
                input_include_in_responses, input_ensembles = await evaluate_ensembles(
                    count_continuation_model_tokens,
                    ctx_vars,
                    em,
                    ensemble_config.input_ensembles,
                    history,
                    max_prompt_length,
                    message_history_ensemble,
                )
                include_in_responses.extend(input_include_in_responses)
            else:
                input_ensembles = history
            faculty_results = FACULTY_NAME_TO_FUNCTION[ensemble_config.faculty](
                em, ensemble_config, input_ensembles
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
                ensemble_config.ensemble_format[0].max_tokens,
            )
            if ensemble_config.faculty == "history":
                ensemble_format = [
                    ensemble_config.ensemble_format[0].model_copy(
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
                    ensemble_config.ensemble_format[0].model_copy(
                        update=dict(max_tokens=local_max_tokens)
                    )
                ] + ensemble_config.ensemble_format[1:]
            underlying_messages, ensemble = await format_ensemble(
                faculty_results, ensemble_format, em.continuation_model, ctx_vars
            )
            if ensemble_config.include_in_responses:
                include_in_responses.extend(underlying_messages)
            ensembles.append(ensemble)
        elif isinstance(ensemble_config, MotifConfig):
            if isinstance(ensemble_config.motif, str):
                ensembles.append(ensemble_config.motif)
            else:
                with open(em.folder / ensemble_config.motif.file) as f:
                    ensembles.append(f.read())
        elif isinstance(ensemble_config, MessageHistoryEnsembleConfig):
            ensembles.append(message_history_ensemble)
        else:
            raise NotImplementedError(f"{type(ensemble_config)} is not implemented")
    return include_in_responses, ensembles


@trace
async def generate_response(em: EmConfig, history: ActionHistory):
    author = Author(em.name)
    include_in_responses, prompt = await prompt_from(em, history)
    continuation_prefix = (
        em.message_history_format.name_prefix(em.name) if em.name_prefix else ""
    )
    recent_messages = await async_take(em.recency_window, history)
    stop_sequences = unique(
        em.stop_sequences
        + [
            # if the continuation prefix is an empty string,
            # it's possible for a stop sequence to be generated
            # immediately. if that's the case, we don't want to
            # prepend a newline to author-based stop sequences
            ("" if continuation_prefix == "" else "\n")
            + em.message_history_format.name_prefix(message.author.name)
            for message in recent_messages
            if message.author.name != em.name
        ]
    )
    for message in include_in_responses:
        yield message
    retry = True
    tries = 0
    while retry and tries < 3:
        tries += 1
        retry = False
        async for reply in get_replies(
            em, prompt, continuation_prefix, em.name, author, stop_sequences
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
    continuation_prefix: str,
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
        **em.continuation_options,
    )
    continuation = response["completions"][0]["text"]
    if (
        callgpt.pick_vendor(em.continuation_model, em.vendors.get_secret_value())
        == "fake-local"
    ):
        to_print = f"{{{callgpt.count_tokens(em.continuation_model, continuation)} tokens omitted}}"
    else:
        trace.continuation(continuation, attr=True)
        to_print = continuation.replace("\n", r"\n")
    print("Continues>>", to_print, "<<Continues", sep="", flush=True)
    log_trace_id_to_console()
    # Todo: Client-side stop sequences
    if em.trim_final_incomplete_sentence:
        if response["completions"][0]["finish_reason"]["reason"] == "length":
            sentences = divide_sentences(continuation)
            tokenizer: nltk.PunktSentenceTokenizer = nltk.data.load(
                f"tokenizers/punkt/english.pickle"
            )
            if not tokenizer.text_contains_sentbreak(sentences[-1]):
                continuation = continuation.removesuffix(
                    tokenizer.sentences_from_text(
                        continuation, realign_boundaries=True
                    )[-1]
                )
    messages = []
    for message in em.message_history_format.parse(continuation_prefix + continuation):
        # accept messages from myself or without prefixes
        if (
            em.prevent_gpt_topic_change
            and message.content.strip() == em.scene_break.strip()
        ):
            break
        # todo: upgrade to support a whitelist
        elif (
            em.name_prefix_optional
            or message.author is None
            or message.author.name == my_name
            or message.author.name in em.extra_valid_output_names
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
) -> tuple[list[Message], str]:
    prompt = None
    local_format = ensemble_format[0]
    underlying_messages = []
    async for i, subensemble in asyncstdlib.enumerate(ensemble):
        if i >= local_format.max_items:
            break
        if isinstance(subensemble, Action):
            underlying_messages.append(subensemble)
            string = local_format.format.render(subensemble)
        else:
            inner_underlying_messages, string = await format_ensemble(
                subensemble, ensemble_format[1:], tokenization_model, ctx_vars
            )
            underlying_messages.extend(inner_underlying_messages)
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
        return underlying_messages, ""
    else:
        return underlying_messages, (
            local_format.header.format(**ctx_vars)
            + prompt
            + local_format.footer.format(**ctx_vars)
        )


T = TypeVar("T")


def unique(iterable: Iterable[T]) -> list[T]:
    return list(dict.fromkeys(iterable))
