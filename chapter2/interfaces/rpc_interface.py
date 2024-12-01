import dataclasses
import glob
import asyncio
import json

from aiohttp import ClientSession, UnixConnector
from jsonrpc_websocket import Server
from abstractinterface import AbstractInterface
from declarations import Message
from pydantic import TypeAdapter


class RPCInterface(AbstractInterface):
    """
    Real-time remote procedure call interface for communicating with a local server.
    Design goal: Mimic the internal API of chapter2 as much as possible

    Stability: Alpha
    Missing: Windows support, telemetry, multi-server support, network listening
    """

    async def start(self):
        self.stop_event = asyncio.Event()
        for fname in glob.glob("/tmp/chapter2*.sock"):
            async with ClientSession(connector=UnixConnector(fname)) as session:
                server = Server(url=f"ws://{self.em_name}", session=session)
                server.ch2.ping = lambda: "pong"
                server.ch2.generate_response = self.handle_request
                try:
                    await server.ws_connect()
                    await self.stop_event.wait()
                finally:
                    await server.close()

    async def handle_request(self, history):

        async def msg_history(history):
            for item in history:
                yield TypeAdapter(Message).validate_json(json.dumps(item))

        messages = []
        async for message in self.generate_response(
            self.base_config.em, msg_history(history)
        ):
            messages.append(dataclasses.asdict(message))
        return messages

    def stop(self, sig, frame):
        self.stop_event.set()
