from ontology import EmConfig, EpisodicMemoryFacultyConfig
from declarations import ActionHistory
from trace import trace


SCHEMA = """
CREATE NODE TABLE Episode(id STRING, author STRING, content STRING, PRIMARY KEY (id)) IF NOT EXISTS
CREATE REL TABLE ProducedWith(FROM Episode to Episode)
"""


@trace
async def episodic_memory_faculty(
    em: EmConfig, faculty_config: EpisodicMemoryFacultyConfig, history: ActionHistory
):
    import kuzu

    db = kuzu.Database(f"./{faculty_config.name}.kuzudb")
    conn = kuzu.Connection(db)  # async is possible
    conn.execute(
        """
        
    """
    )

    pass
