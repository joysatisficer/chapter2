import asyncio

from types import FrameType
from typing import Optional

import uvicorn


class RapidShutdownUvicornServer(uvicorn.Server):
    async def main_loop(self) -> None:
        async def inner_main_loop():
            try:
                await super(RapidShutdownUvicornServer, self).main_loop()
            except asyncio.CancelledError:
                return

        self._inner_main_loop_task = asyncio.create_task(inner_main_loop())
        await asyncio.gather(self._inner_main_loop_task)

    def handle_exit(self, sig: int, frame: Optional[FrameType]) -> None:
        super().handle_exit(sig, frame)
        self._inner_main_loop_task.cancel()
