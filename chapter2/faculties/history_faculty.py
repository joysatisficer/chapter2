from ontology import HistoryFacultyConfig, EmConfig

from declarations import Message, Author, ActionHistory

# loads and formats the history


async def history_faculty(
    history: ActionHistory, faculty_config: HistoryFacultyConfig, em: EmConfig
):
    filename = faculty_config.filename
    directory = str(em.folder / f"{filename}")
    with open(directory, "r") as f:
        transcript = f.read()
    async for message in transcript_to_messages(transcript, faculty_config, em):
        yield message


async def transcript_to_messages(
    transcript: str, faculty_config: HistoryFacultyConfig, em: EmConfig
) -> ActionHistory:
    messages = faculty_config.input_format.parse(transcript)
    for message in reversed(messages):
        if not message.author:
            yield message
        elif (
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
