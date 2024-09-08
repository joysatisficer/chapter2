import time
import hashlib
import asyncio

import intermodel.callgpt
import openai
import aiohttp

from interfaces.discord_interface import DiscordInterface
from ontology import DiscordGenerateAvatarAddonConfig


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
            config, _ = await self.get_config(None)
            match addon_config.image_vendor:
                case "novelai":
                    contents = await self.generate_avatar_novelai()
                case "openai":
                    contents = await self.generate_avatar_openai()
            avatars_folder = config.em.folder / "avatars"
            avatars_folder.mkdir(exist_ok=True)
            hasher = hashlib.sha256(usedforsecurity=False)
            hasher.update(contents)
            with open(avatars_folder / (hasher.hexdigest() + ".png"), "wb") as f:
                f.write(contents)
            await self.user.edit(avatar=contents)
            with open(config.em.folder / "avatar_changed_at", "w") as f:
                f.write(str(int(start_time)))

        async def generate_avatar_novelai(self) -> bytes:
            import novelai_api
            from novelai_api.ImagePreset import (
                ImageModel,
                ImagePreset,
                ImageResolution,
                UCPreset,
            )

            config, _ = await self.get_config(None)
            async with aiohttp.ClientSession() as session:
                api = novelai_api.NovelAIAPI(session)
                await api.high_level.login_with_token(
                    config.em.novelai_api_key.get_secret_value()
                )
                preset = ImagePreset.from_v3_config()
                preset.resolution = ImageResolution.Small_Square
                preset.scale = addon_config.scale
                preset.seed = 0
                image_model = ImageModel(addon_config.image_model)
                if image_model in (ImageModel.Anime_v3, ImageModel.Anime_v2):
                    preset.uc_preset = UCPreset.Preset_Heavy
                resp_aiter = api.high_level.generate_image(
                    addon_config.prompt, image_model, preset
                )
                # each iteration is one for loop
                async for _, bytestring in resp_aiter:
                    return bytestring

        async def generate_avatar_openai(self) -> bytes:
            config = await self.get_config(None)
            try:
                model = addon_config.image_model
                openai_image_response = await openai.Image.acreate(
                    model=model,
                    prompt=addon_config.prompt,
                    # todo: make intermodel support dall-e
                    api_key=config.vendors["openai"].config["openai_api_key"],
                )
            except openai.error.InvalidRequestError:
                raise  # todo: propagate exception to entire event loop
            # todo: retry on connectionerror with backoff
            async with aiohttp.ClientSession() as session:
                async with session.get(openai_image_response.data[0].url) as response:
                    contents = await response.read()
            return contents

        @log_async_task_exceptions
        async def regenerate_avatar_loop(self, interval):
            config, _ = await self.get_config(None)
            while True:
                # todo: log exceptions and retry
                try:
                    with open(config.em.folder / "avatar_changed_at") as f:
                        timestamp = int(f.read())
                except FileNotFoundError:
                    timestamp = 0
                time_elapsed = time.time() - timestamp
                await asyncio.sleep(interval - time_elapsed)
                await self.generate_and_set_avatar()

        def stop(self, sig, frame):
            super().stop(sig, frame)
            if hasattr(self, "regenerate_avatar_task"):
                self.regenerate_avatar_task.cancel()

    return DiscordGenerateAvatarAddon


def log_async_task_exceptions(decorated):
    async def log_async_task_exceptions(*args, **kwargs):
        try:
            return await decorated(*args, **kwargs)
        except Exception as e:
            import traceback

            traceback.print_exc()
            raise

    return log_async_task_exceptions
