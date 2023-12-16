import asyncio
from abc import ABC, abstractmethod
from typing import Callable, Awaitable

from resolve_config import Config, InterfaceConfig
from declarations import GenerateResponse

GetDiscordConfig = Callable[["discord.abc.MessageableChannel"], Awaitable[Config]]


# todo: interface-specific configuration
class AbstractInterface(ABC):
    @abstractmethod
    def __init__(
        self,
        get_discord_config: GetDiscordConfig,
        generate_response: GenerateResponse,
        em_name: str,
        interface_config: InterfaceConfig,
    ):
        self.get_config: GetDiscordConfig = get_discord_config
        self.generate_response: GenerateResponse = generate_response
        self.em_name = em_name
        # in 3.12, we can use type variables to replace this with a super() call
        self.interface_config = interface_config
        self.finalized_shutdown = asyncio.Event()

    @abstractmethod
    async def start(self):
        pass

    """
    Handle SIGINT. If your interface uses third-party code, please stop it from using
    signal.signal() and instead call its shutdown code in this method. You can set the
    method that sets signal.signal() to be a no-op instead. Example:
    
        self.uv_server = uvicorn.Server(uv_config)
        self.uv_server.install_signal_handlers = lambda: None
    """

    @abstractmethod
    def stop(self, sig, frame):
        pass
