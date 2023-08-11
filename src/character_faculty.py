from declarations import Faculty
from resolve_config import Config
from chr_loader import load_chr
from retriever import KNNIndex


class CharacterFaculty(Faculty):
    def __init__(self, config: Config):
        super().__init__(config)
        self.index = KNNIndex("intfloat/e5-large-v2")

        atoms = load_chr(str(config.em_folder / f"{config.name}.chr"))

    async def fetch(self, query):
        pass
