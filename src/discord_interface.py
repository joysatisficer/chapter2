import asyncio
import contextlib
import signal
import time
from typing import AsyncIterable, TypeVar, Callable, Awaitable

import discord
import yaml

from discord_improved import ScheduleTyping, parse_discord_content
from declarations import GenerateResponse, Message, UserID, Author, JSON

GetDiscordMetadata = Callable[["discord.abc.MessageableChannel"], Awaitable[JSON]]


class DiscordInterface(discord.Client):
    MAX_CONCURRENT_MESSAGES = 100_000

    def __init__(
        self,
        agent_name: str,
        generate_response: GenerateResponse,
        get_discord_metadata: GetDiscordMetadata,
    ) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.agent_name = agent_name
        self.generate_response: GenerateResponse = generate_response
        self.get_metadata: GetDiscordMetadata = get_discord_metadata
        self.message_semaphore = asyncio.BoundedSemaphore(self.MAX_CONCURRENT_MESSAGES)
        self.pending_shutdown = False
        signal.signal(signal.SIGINT, self.shutdown)

    async def on_message(self, message: discord.Message) -> None:
        async with self.handle_exceptions(message):
            if await self.parse_continue_command(message):
                command_message = message
            else:
                command_message = None
            metadata = await self.get_metadata(message.channel)
            if not await self.should_reply(message, metadata):
                return
            my_user_id = UserID(self.user.id, "discord")
            # PyCharm's type checker incorrectly infers an async-for generator
            # as a Generator when it is an AsyncIterable
            # noinspection PyTypeChecker
            response_messages = self.generate_response(
                my_user_id,
                (
                    await self.discord_message_to_message(message)
                    async for message in message.channel.history(
                        limit=None, before=command_message
                    )
                    if not await self.parse_continue_command(message)
                ),
                metadata,
            )
            async with ScheduleTyping(message.channel):
                async for reply_message in response_messages:
                    if reply_message.author.user_id == my_user_id and not isempty(
                        reply_message.message
                    ):
                        await wait_until_timestamp(
                            reply_message.timestamp, message.channel.typing
                        )
                        await message.channel.send(reply_message.message)
            if command_message is not None:
                await command_message.delete()

    async def discord_message_to_message(self, message: discord.Message) -> Message:
        return Message(
            Author(UserID(message.author.id, "discord"), message.author.name),
            await parse_discord_content(message),
            message.created_at.timestamp(),
        )

    async def parse_continue_command(self, message):
        return message.content.strip() == "/continue" or message.content.startswith(
            "m continue"
        )

    async def should_reply(self, message: discord.Message, metadata: JSON) -> bool:
        return (
            message.author != self.user
            and (
                not isinstance(message.channel, discord.abc.GuildChannel)
                or message.channel.permissions_for(message.guild.me).send_messages
            )
            and not (
                metadata["discord_mute"] is True
                or metadata["discord_mute"] == self.agent_name
                or (
                    isinstance(metadata["discord_mute"], list)
                    and self.agent_name in metadata["discord_mute"]
                )
            )
        )

    def shutdown(self, sig, frame):
        self.pending_shutdown = True
        if self.message_semaphore._value == self.MAX_CONCURRENT_MESSAGES:
            raise KeyboardInterrupt()

    @contextlib.asynccontextmanager
    async def handle_exceptions(self, message: discord.Message):
        try:
            async with self.message_semaphore:
                yield None
        except Exception:
            await message.add_reaction("⚠")
            print(
                "exception in channel",
                format_cli_link(
                    message.jump_url,
                    # if the message is deleted, the url will still head to the channel
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


async def get_channel_metadata_from_topic_as_yaml(
    channel: "discord.abc.MessageableChannel",
) -> dict:
    """
    Reads a channel description, extracting yaml metadata from it.
    """
    if hasattr(channel, "topic"):
        topic = channel.topic
    elif hasattr(channel, "parent"):
        topic = channel.parent.topic
    else:
        topic = None
    if topic is not None and "---" in topic:
        metadata = yaml.safe_load(channel.topic.split("---")[1])
    else:
        metadata = {}
    defaults = {"discord_mute": False}
    return override_with(defaults, metadata)


def override_with(items: dict, updates: dict):
    """
    Updates a dict, if a value is a dict, uses recursion.

    Returns:
        dict: a new dictionary with updated values
    """
    result = dict(items)
    for key, value in updates.items():
        if isinstance(value, dict):
            if key not in result:
                result[key] = {}
            result[key] = override_with(result[key], value)
        else:
            result[key] = value
    return result


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
