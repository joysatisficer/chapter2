from declarations import ActionHistory, Message, Author
from ontology import AirtableNotesFacultyConfig, EmConfig
from asgiref.sync import sync_to_async


async def airtable_notes_faculty(
    history: ActionHistory, faculty_config: AirtableNotesFacultyConfig, em: EmConfig
):
    from pyairtable import Api

    api = Api(faculty_config.api_token.get_secret_value())
    table = api.table(faculty_config.base_id, faculty_config.table_id)
    for row in await sync_to_async(table.all, thread_sensitive=False)():
        field = row["fields"]
        yield Message(Author(em.name), field["Note"])
