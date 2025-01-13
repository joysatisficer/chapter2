from ontology import HistoryFacultyConfig, EmConfig

from declarations import Message, Author, ActionHistory, Faculty

# loads and formats the history


async def history_faculty(
    history: ActionHistory, faculty_config: HistoryFacultyConfig, em: EmConfig
):
    if faculty_config.filename is None:
        filename = "history.txt"
    else:
        filename = faculty_config.filename
    directory = str(em.folder / f"{filename}")
    with open(directory, "r") as f:
        messages = faculty_config.input_format.parse(f.read())
    for message in reversed(messages):
        if (
            faculty_config.nickname is not None
            and message.author.name == faculty_config.nickname
        ):
            yield Message(Author(em.name), message.content, type=message.type)
        elif message.author.name in faculty_config.nicknames:
            yield Message(
                Author(faculty_config.nicknames[message.author.name]),
                message.content,
                type=message.type,
            )
        else:
            yield message
