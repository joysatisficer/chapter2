from declarations import ActionHistory, Message, Author
from ontology import AirtableNotesFacultyConfig, EmConfig, AirtableConfig
from asgiref.sync import sync_to_async
from trace import trace


def get_airtable(airtable_config: AirtableConfig):
    from pyairtable import Api

    api = Api(airtable_config.api_token.get_secret_value())
    return api.table(airtable_config.base_id, airtable_config.table_id)


@trace
async def airtable_notes_faculty(
    history: ActionHistory, faculty_config: AirtableNotesFacultyConfig, em: EmConfig
):
    for row in await sync_to_async(
        get_airtable(faculty_config.airtable).all, thread_sensitive=False
    )():
        field = row["fields"]
        yield Message(Author(em.name), field["Note"])
