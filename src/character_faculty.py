import asyncio
from functools import cache

from aioitertools.more_itertools import take as async_take
from asgiref.sync import sync_to_async

from declarations import Message, Author, MessageHistory, Faculty
from resolve_config import Config
from chr_loader import load_chr
from retriever import KNNIndex
from message_formats import irc_message_format, colon_message_format

# todo: turn faculties into functions, make the KNN index maker a cached function using functools cache
# todo: read character folder in the impure shell


async def character_faculty(history: MessageHistory, config: Config):
    atoms = load_chr(str(config.em_folder / f"{config.name}.chr"))
    formatted_atoms = []
    for atom in atoms:
        for message in irc_message_format.parse(atom):
            formatted_atoms.append(colon_message_format.render(message).strip())
    # todo: options for non-KNN indexes
    index = await create_index(KNNIndex, config.representation_model, formatted_atoms)
    messages = await async_take(
        config.character_faculty_recent_message_attention, history
    )
    query = ""
    for message in messages[::-1]:
        query += colon_message_format.render(message)
    results = await index.query(query.replace("\n", " "), 1000)
    messages = []
    for item in results:
        messages.extend(colon_message_format.parse(item))
    return messages


@sync_to_async
@cache
def create_index(self, cls, representation_model, formatted_atoms):
    index = cls(representation_model)
    index.add_data(formatted_atoms)
    index.freeze()
    return index
