from declarations import ActionHistory
from ontology import SimFacultyConfig, EmConfig


async def sim_faculty(
    em: EmConfig, faculty_config: SimFacultyConfig, history: ActionHistory
):
    from generate_response import generate_response

    async for action in generate_response(
        faculty_config.em.model_copy(update={"vendors": em.vendors}), history
    ):
        yield action
