import asyncio
import contextlib
import os
import sys
import signal
import time
import urllib.parse
from typing import Callable, Awaitable, Optional, AsyncIterable

import discord
import yaml
from pydantic import ValidationError

from util.discord_improved import ScheduleTyping, parse_discord_content
from declarations import GenerateResponse, Message, UserID, Author, JSON
from abstractinterface import GetDiscordConfig
from resolve_config import Config


class DiscordInterface(discord.Client):
    MAX_CONCURRENT_MESSAGES = 100_000

    def __init__(
        self,
        get_discord_config: GetDiscordConfig,
        generate_response: GenerateResponse,
        agent_name: str,
    ):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.get_config: GetDiscordConfig = get_discord_config
        self.generate_response: GenerateResponse = generate_response
        self.agent_name = agent_name
        self.message_semaphore = asyncio.BoundedSemaphore(self.MAX_CONCURRENT_MESSAGES)
        self.pending_shutdown = False

    async def on_message(self, message: discord.Message) -> None:
        if await self.parse_continue_command(message):
            command_message = message
            message_to_respond_to = [
                message async for message in message.channel.history(limit=2)
            ][1]
        else:
            command_message = None
            message_to_respond_to = message
        async with self.handle_exceptions(message_to_respond_to):
            try:
                try:
                    config = await self.get_config(message.channel)
                except (ValueError, ValidationError) as exc:
                    raise ConfigError() from exc
                if not await self.should_reply(message, config):
                    command_message = None
                    return
                my_user_id = UserID(self.user.id, "discord")

                # PyCharm's type checker incorrectly infers an async-for
                # generator as a Generator when it is an AsyncIterable
                # noinspection PyTypeChecker
                class MessageHistoryIterable:
                    def __aiter__(_) -> AsyncIterable:
                        async def inner():
                            nonlocal message
                            async for message in message.channel.history(
                                limit=None, before=command_message
                            ):
                                if not await self.parse_continue_command(message):
                                    yield await self.discord_message_to_message(
                                        config, message
                                    )

                        return inner()

                # noinspection PyTypeChecker
                response_messages = self.generate_response(
                    my_user_id,
                    MessageHistoryIterable(),
                    config,
                )
                async with ScheduleTyping(message.channel):
                    async for reply_message in response_messages:
                        if reply_message.author.user_id == my_user_id and not isempty(
                            reply_message.content
                        ):
                            await wait_until_timestamp(
                                reply_message.timestamp, message.channel.typing
                            )
                            await message.channel.send(reply_message.content)
            finally:
                if command_message is not None:
                    await command_message.delete()

    async def discord_message_to_message(
        self, config, message: discord.Message
    ) -> Message:
        if message.author.id == self.user.id:
            author_name = config.name
        else:
            author_name = message.author.name
        return Message(
            Author(author_name, UserID(message.author.id, "discord")),
            await parse_discord_content(message),
            message.created_at.timestamp(),
        )

    async def parse_continue_command(self, message):
        return message.content.strip() == "/continue" or message.content.startswith(
            "m continue"
        )

    async def should_reply(self, message: discord.Message, config: Config) -> bool:
        return (
            message.author != self.user
            and (
                not isinstance(message.channel, discord.abc.GuildChannel)
                or message.channel.permissions_for(message.guild.me).send_messages
            )
            and not (
                config.discord_mute is True
                or config.discord_mute == self.agent_name
                or (
                    isinstance(config.discord_mute, list)
                    and self.agent_name in config.discord_mute
                )
            )
            and not (
                config.thread_mute is True
                and message.channel.type == discord.ChannelType.public_thread
            )
        )

    @contextlib.asynccontextmanager
    async def handle_exceptions(self, message: discord.Message):
        try:
            async with self.message_semaphore:
                yield None
        except ConfigError as exc:
            await message.add_reaction("⚙️")
            # if the message is deleted, the url will still head to the channel
            print(
                "bad config in channel",
                format_cli_link(
                    message.jump_url,
                    f"#{message.channel.name}",
                ),
                get_channel_topic(message.channel),
            )
            raise exc.__cause__
        except Exception as exc:
            await message.add_reaction("⚠")
            if isinstance(exc, ConnectionError):
                await message.add_reaction("📵")
            print(
                "exception in channel",
                format_cli_link(
                    message.jump_url,
                    f"#{message.channel.name}",
                ),
            )
            raise
        finally:
            if (
                self.pending_shutdown
                and self.message_semaphore._value == self.MAX_CONCURRENT_MESSAGES
            ):
                await self.close()

    async def on_ready(self):
        if len(self.guilds) == 0:
            print(f"Invite the bot: {self.get_invite_link()}")
        await self.change_presence(
            activity=discord.Streaming(
                name="Chapter 2", url="https://www.youtube.com/watch?v=ESx_hy1n7HA"
            )
        )
        print("Discord interface ready")

    def get_invite_link(self):
        if self.user.id is None:
            raise ValueError("Tried to get invite link before bot user ID is known")
        return "https://discord.com/api/oauth2/authorize?" + urllib.parse.urlencode(
            {"client_id": self.user.id, "permissions": 536879168, "scope": "bot"}
        )

    async def start(self, token: str = None, *args, **kwargs) -> None:
        if token is None:
            token = (await self.get_config(None)).discord_token
        return await super().start(token, *args, **kwargs)

    def stop(self, sig, frame):
        self.pending_shutdown = True
        asyncio.create_task(self.close())


async def get_yaml_from_channel(
    channel: "discord.abc.MessageableChannel",
) -> JSON:
    """
    Reads a channel description, extracting yaml from it.
    """
    topic = get_channel_topic(channel)
    if topic is not None and "---" in topic:
        return yaml.safe_load(topic.split("---")[1])
    else:
        return {}


def get_channel_topic(
    channel: "discord.abc.MessageableChannel",
) -> Optional[str]:
    if hasattr(channel, "topic"):
        return channel.topic
    elif hasattr(channel, "parent"):
        return channel.parent.topic
    else:
        return None


async def wait_until_timestamp(timestamp, coroutine):
    current_time = time.time()
    if timestamp > current_time:
        # to reduce latency, only send a typing event if there is an actual delay
        async with coroutine():
            await asyncio.sleep(timestamp - current_time)


def isempty(string):
    return string == "" or string.isspace()


def format_cli_link(uri, label=None):
    if label is None:
        label = uri
    parameters = ""

    # OSC 8 ; params ; URI ST <name> OSC 8 ;; ST
    escape_mask = "\033]8;{};{}\033\\{}\033]8;;\033\\"

    return escape_mask.format(parameters, uri, label)


class ConfigError(ValueError):
    pass
