from abc import ABC, abstractmethod
from typing import Callable, Awaitable

from resolve_config import Config
from declarations import GenerateResponse

GetDiscordConfig = Callable[["discord.abc.MessageableChannel"], Awaitable[Config]]


# todo: interface-specific configuration
class AbstractInterface(ABC):
    @abstractmethod
    def __init__(
        self,
        get_discord_config: GetDiscordConfig,
        generate_response: GenerateResponse,
        agent_name: str,
    ):
        pass

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
