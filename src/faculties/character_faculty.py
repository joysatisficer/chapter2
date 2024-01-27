import asyncio
from functools import cache
from collections import Counter

from asyncstdlib.functools import cache as async_cache
from aioitertools.more_itertools import take as async_take
from asgiref.sync import sync_to_async

from declarations import Message, Author, ActionHistory, Faculty
from resolve_config import Config, CharacterFacultyConfig
from chr_loader import load_chr
from retriever import KNNIndex
from message_formats import IRCMessageFormat, ColonMessageFormat

# todo: turn faculties into functions, make the KNN index maker a cached function using functools cache
# todo: read character folder in the impure shell


async def character_faculty(
    history: ActionHistory, faculty_config: CharacterFacultyConfig, config: Config
):
    strings = load_chr(str(config.em_folder / f"{config.name}.chr"))
    representations = []
    indexed_messages = []
    for string in strings:
        for message in IRCMessageFormat.parse(string):
            representations.append(ColonMessageFormat.render(message).strip())
            indexed_messages.append(message)

    dedup_representations, dedup_indexed_messages = remove_duplicate_representations(
        tuple(representations), tuple(indexed_messages)
    )

    # todo: options for non-KNN indexes
    index = await create_index(
        KNNIndex,
        config.representation_model,
        tuple(dedup_representations),
        tuple(dedup_indexed_messages),
    )
    messages = await async_take(faculty_config.recent_message_attention, history)
    query = ""
    for message in messages[::-1]:
        query += ColonMessageFormat.render(message)
    results = await index.query(query.replace("\n", " "), 1000)
    for message in results:
        yield message


@cache
def remove_duplicate_representations(representations, indexed):
    first_instance = {}
    for representation, index in zip(representations, indexed):
        if representation not in first_instance:
            first_instance[representation] = index
    return tuple(first_instance.keys()), tuple(first_instance.values())


@async_cache
async def create_index(
    cls, representation_model, representations: tuple[str], indexed_messages: tuple[str]
):
    index = cls(representation_model)
    if len(list(representations)) == 0:
        print("warn: empty character")
    await index.add_data(list(representations), list(indexed_messages))
    index.freeze()
    return index
