import time
import hashlib
import asyncio

import intermodel.callgpt
import openai
import aiohttp

from interfaces.discord_interface import DiscordInterface
from resolve_config import DiscordGenerateAvatarAddonConfig


def discord_generate_avatar(addon_config: DiscordGenerateAvatarAddonConfig):
    class DiscordGenerateAvatarAddon(DiscordInterface):
        async def on_ready(self):
            await super().on_ready()
            if self.user.avatar is None:
                await self.generate_and_set_avatar()
            if addon_config.regenerate_every is not None:
                self.regenerate_avatar_task = asyncio.create_task(
                    self.regenerate_avatar_loop(addon_config.regenerate_every)
                )

        @log_async_task_exceptions
        async def generate_and_set_avatar(self):
            start_time = time.time()
            config = await self.get_config(None)
            try:
                model = 'dall-e-3'
                openai_image_response = await openai.Image.acreate(
                    model=model,
                    prompt=addon_config.prompt,
                    api_key=config.vendors[intermodel.callgpt.pick_vendor(model)].config['openai_api_key']
                )
            except openai.error.InvalidRequestError:
                raise  # todo: propagate exception to entire event loop
            # todo: retry on connectionerror with backoff
            async with aiohttp.ClientSession() as session:
                async with session.get(openai_image_response.data[0].url) as response:
                    contents = await response.read()
            avatars_folder = config.em_folder / "avatars"
            avatars_folder.mkdir(exist_ok=True)
            hasher = hashlib.sha256(usedforsecurity=False)
            hasher.update(contents)
            with open(avatars_folder / (hasher.hexdigest() + '.png'), "wb") as f:
                f.write(contents)
            await self.user.edit(avatar=contents)
            with open(config.em_folder / "avatar_changed_at", "w") as f:
                f.write(str(int(start_time)))

        async def regenerate_avatar_loop(self, interval):
            config = await self.get_config(None)
            while True:
                # todo: log exceptions and retry
                try:
                    with open(config.em_folder / "avatar_changed_at") as f:
                        timestamp = int(f.read())
                except FileNotFoundError:
                    timestamp = time.time()
                time_elapsed = time.time() - timestamp
                await asyncio.sleep(interval - time_elapsed)
                await self.generate_and_set_avatar()

        def stop(self, sig, frame):
            super().stop(sig, frame)
            if hasattr(self, 'regenerate_avatar_task'):
                self.regenerate_avatar_task.cancel()

    return DiscordGenerateAvatarAddon


async def log_async_task_exceptions(awaitable):
    try:
        return await awaitable
    except Exception as e:
        print("Unhandled exception")
        raise
