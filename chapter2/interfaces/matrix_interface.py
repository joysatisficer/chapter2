from nio import AsyncClient, MatrixRoom, RoomMessageText

from abstractinterface import AbstractInterface
from declarations import GenerateResponse
from ontology import InterfaceConfig


class MatrixInterface(AbstractInterface):
    async def start(self):
        pass

    def stop(self, sig, frame):
        pass
