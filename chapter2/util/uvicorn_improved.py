import asyncio

from types import FrameType
from typing import Optional

import uvicorn


class RapidShutdownUvicornServer(uvicorn.Server):
    async def main_loop(self) -> None:
        if hasattr(self, "on_ready"):
            self.on_ready()
        try:
            await super().main_loop()
        except asyncio.CancelledError:
            return

    def handle_exit(self, sig: int, frame: Optional[FrameType]) -> None:
        super().handle_exit(sig, frame)
        self._inner_main_loop_task.cancel()
