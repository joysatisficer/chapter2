from declarations import ActionHistory, UserID
from ontology import SimFacultyConfig, EmConfig


async def sim_faculty(
    history: ActionHistory, faculty_config: SimFacultyConfig, em: EmConfig
):
    from generate_response import generate_response

    my_user_id = UserID(em.name, "sim")
    async for action in generate_response(my_user_id, history, faculty_config.em):
        yield action
