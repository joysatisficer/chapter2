import asyncio
import uvicorn


class RapidShutdownUvicornServer(uvicorn.Server):
    async def main_loop(self) -> None:
        if hasattr(self, "on_ready"):
            self.on_ready()
        try:
            await super().main_loop()
        except asyncio.CancelledError:
            return
