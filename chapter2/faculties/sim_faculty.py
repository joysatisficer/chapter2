from declarations import ActionHistory
from ontology import SimFacultyConfig, EmConfig, get_defaults


async def sim_faculty(
    em: EmConfig, faculty_config: SimFacultyConfig, history: ActionHistory
):
    from generate_response import generate_response

    if faculty_config.inherit:
        em_to_sim = EmConfig(**{**em.model_dump(), **faculty_config.em})
    else:
        em_to_sim = EmConfig(**faculty_config.em)

    async for action in generate_response(em_to_sim, history):
        yield action
