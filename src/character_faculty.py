from aioitertools.more_itertools import take as async_take

from declarations import Message, Author, MessageHistory, Faculty
from resolve_config import Config
from chr_loader import load_chr
from retriever import KNNIndex
from message_formats import irc_message_format, colon_message_format


class CharacterFaculty(Faculty):
    def __init__(self, config: Config):
        super().__init__(config)
        self.index = KNNIndex(config.representation_model)
        atoms = load_chr(str(config.em_folder / f"{config.name}.chr"))
        formatted_atoms = []
        for atom in atoms:
            for message in irc_message_format.parse(atom):
                formatted_atoms.append(colon_message_format.render(message).strip())
        self.index.add_data(formatted_atoms)

    async def fetch(self, history: MessageHistory, config: Config):
        messages = await async_take(
            config.character_faculty_recent_message_attention, history
        )
        query = ""
        for message in messages[::-1]:
            query += colon_message_format.render(message)
        print(query)
        results = self.index.query(query.replace("\n", " "), 1000)
        messages = []
        for item in results:
            messages.extend(colon_message_format.parse(item))
        return messages
