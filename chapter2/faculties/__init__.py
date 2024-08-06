from faculties.character_faculty import character_faculty
from faculties.sim_faculty import sim_faculty
from faculties.exa_search_faculty import exa_search_faculty

FACULTY_NAME_TO_FUNCTION = {
    "character": character_faculty,
    "sim": sim_faculty,
    "exa_search": exa_search_faculty,
    # deprecated names
    "metaphor_search": exa_search_faculty,
}
