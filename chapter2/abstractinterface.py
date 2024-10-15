import asyncio
from abc import ABC, abstractmethod
from typing import Callable, Awaitable

from ontology import Config, InterfaceConfig
from declarations import GenerateResponse


# todo: interface-specific configuration
class AbstractInterface(ABC):
    def __init__(
        self,
        base_config: Config,
        generate_response: GenerateResponse,
        em_name: str,
        interface_config: InterfaceConfig,
    ):
        self.base_config = base_config
        self.generate_response: GenerateResponse = generate_response
        self.em_name = em_name
        # in 3.12, we can use type variables to replace this with a super() call
        self.iface_config = interface_config

    @abstractmethod
    async def start(self):
        pass

    @abstractmethod
    def stop(self, sig, frame):
        """
        Handle SIGINT. If your interface uses third-party code, please stop it from using
        signal.signal() and instead call its shutdown code in this method. You can set the
        method that sets signal.signal() to be a no-op instead. Example:

            self.uv_server = uvicorn.Server(uv_config)
            self.uv_server.install_signal_handlers = lambda: None
        """
        pass
