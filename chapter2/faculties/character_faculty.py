from functools import cache

from asyncstdlib.functools import cache as async_cache
from aioitertools.more_itertools import take as async_take

from declarations import Message, Author, ActionHistory, Faculty
from ontology import CharacterFacultyConfig, EmConfig
from chr_loader import load_chr
from retriever import KNNIndex, SVMIndex
from message_formats import ColonMessageFormat
from util.asyncutil import eager_iterable_to_async_iterable
from trace import trace

# todo: read character folder in the impure shell


@trace
async def character_faculty(
    history: ActionHistory, faculty_config: CharacterFacultyConfig, em: EmConfig
):
    if faculty_config.name is None:
        character_name = em.name
    else:
        character_name = faculty_config.name
    strings = load_chr(
        str(em.folder / f"{character_name}.chr"), faculty_config.chunk_size
    )
    representations = []
    indexed_messages = []
    for string in strings:
        representation = ""
        messages = faculty_config.input_format.parse(string)
        for message_tuple in messages:
            representation += ColonMessageFormat().render(message_tuple).strip() + " "
        if representation != "":
            representations.append(representation)
            indexed_messages.append(tuple(messages))

    dedup_representations, dedup_indexed_messages = remove_duplicate_representations(
        tuple(representations), tuple(indexed_messages)
    )

    # todo: options for non-KNN indexes
    index = await create_index(
        {"knn": KNNIndex, "svm": SVMIndex}[faculty_config.retriever.ranking_metric],
        em.representation_model,
        tuple(dedup_representations),
        tuple(dedup_indexed_messages),
    )
    messages = await async_take(faculty_config.recent_message_attention, history)
    query = ""
    for message_tuple in messages[::-1]:
        query += ColonMessageFormat().render(message_tuple)
    results = await index.query(query.replace("\n", " "), 1000)
    for message_tuple in results:
        yield eager_iterable_to_async_iterable(message_tuple)


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
