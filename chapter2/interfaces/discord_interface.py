import asyncio
import contextlib
import time
import urllib.parse
import random
from typing import Self

import discord
import discord.http
import yaml
from pydantic import ValidationError

from util.asyncutil import async_generator_to_reusable_async_iterable, run_task
from util.discord_improved import ScheduleTyping, parse_discord_content
from declarations import GenerateResponse, Message, UserID, Author, JSON
from abstractinterface import GetDiscordConfig
from resolve_config import Config, DiscordInterfaceConfig


class DiscordInterface(discord.Client):
    MAX_CONCURRENT_MESSAGES = 100_000

    def __init__(
        self,
        get_discord_config: GetDiscordConfig,
        generate_response: GenerateResponse,
        em_name: str,
        interface_config: DiscordInterfaceConfig,
    ):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        if (
            interface_config.proxy_url is None
            or not interface_config.proxy_url.startswith("http")
        ):
            super().__init__(intents=intents)
        else:
            super().__init__(intents=intents, proxy=interface_config.proxy_url)
        self.get_config: GetDiscordConfig = get_discord_config
        self.generate_response: GenerateResponse = generate_response
        self.em_name = em_name
        self.interface_config = interface_config
        self.message_semaphore = asyncio.BoundedSemaphore(self.MAX_CONCURRENT_MESSAGES)
        self.pending_shutdown = False
        if (
            self.interface_config.proxy_url is not None
            and self.interface_config.proxy_url.startswith("socks")
        ):
            from aiohttp_socks import ProxyConnector
            from discord.state import ConnectionState

            self.http = discord.http.HTTPClient(
                self.loop, ProxyConnector.from_url(interface_config.proxy_url)
            )
            self._connection: ConnectionState[Self] = self._get_state(intents=intents)
            self._connection.shard_count = self.shard_count
            self._connection._get_websocket = self._get_websocket
            self._connection._get_client = lambda: self

    async def on_message(self, message: discord.Message) -> None:
        if is_continue_command(message.content):
            command_message = message
            message_to_react_to = [
                message async for message in message.channel.history(limit=2)
            ][1]
        elif is_mu_command(message.content):
            command_message = message
            async for this_message in message.channel.history(before=message):
                if this_message.author.id == self.user.id:
                    await this_message.delete()
                else:
                    break
            message_to_react_to = [
                message async for message in message.channel.history(limit=2)
            ][1]
        else:
            command_message = None
            message_to_react_to = message
        async with self.handle_exceptions(message_to_react_to):
            try:
                config = await self.get_config(message.channel)
            except (ValueError, ValidationError) as exc:
                raise ConfigError() from exc
            if not await self.should_reply(message, config):
                return
            if (
                config.discord_random_threshold < 1
                and random.random() < config.discord_random_threshold
                and not self.user.mentioned_in(message)
            ):
                return
            try:
                my_user_id = UserID(str(self.user.id), "discord")

                async def message_history():
                    nonlocal message
                    async for this_message in message.channel.history(
                        limit=None, before=command_message
                    ):
                        if not is_continue_command(this_message.content):
                            yield await self.discord_message_to_message(
                                config, this_message
                            )

                response_messages = self.generate_response(
                    my_user_id,
                    async_generator_to_reusable_async_iterable(message_history),
                    config,
                )
                async with ScheduleTyping(
                    message.channel, typing=config.discord_send_typing
                ):
                    async for reply_message in response_messages:
                        if reply_message.author.user_id == my_user_id and not isempty(
                            reply_message.content
                        ):
                            await wait_until_timestamp(
                                reply_message.timestamp, message.channel.typing
                            )
                            await message.channel.send(
                                realize_pings(message.channel, reply_message.content)
                            )
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
            Author(author_name, UserID(str(message.author.id), "discord")),
            await parse_discord_content(message),
            message.created_at.timestamp(),
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
                or config.discord_mute == self.em_name
                or (
                    isinstance(config.discord_mute, list)
                    and self.em_name in config.discord_mute
                )
            )
            and not (
                config.thread_mute
                and message.channel.type == discord.ChannelType.public_thread
            )
            and (not config.only_reply_on_ping or self.user.mentioned_in(message))
        )

    @contextlib.asynccontextmanager
    async def handle_exceptions(self, message: discord.Message):
        config = await self.get_config(None)
        try:
            async with self.message_semaphore:
                yield None
        except ConfigError as exc:
            if config.end_to_end_test:
                self.end_to_end_test_fail = True
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
            if config.end_to_end_test:
                self.end_to_end_test_fail = True
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
            print(exc)
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
                name="Chapter 2", url="https://www.youtube.com/watch?v=kCWX6yxg-iA"
            )
        )
        print("Discord interface ready")
        if (await self.get_config(None)).end_to_end_test:
            run_task(self.end_to_end_test())

    def get_invite_link(self):
        if self.user.id is None:
            raise ValueError("Tried to get invite link before bot user ID is known")
        return "https://discord.com/api/oauth2/authorize?" + urllib.parse.urlencode(
            {"scope": "bot", "permissions": 536879168, "client_id": self.user.id}
        )

    async def start(self, token: str = None, *args, **kwargs) -> None:
        if token is None:
            token = self.interface_config.auth
        return await super().start(token, *args, **kwargs)

    def stop(self, sig, frame):
        self.pending_shutdown = True
        asyncio.create_task(self.handle_shutdown())

    async def handle_shutdown(self):
        if self.message_semaphore._value == self.MAX_CONCURRENT_MESSAGES:
            await self.close()
        self.pending_shutdown = True

    async def end_to_end_test(self):
        config = await self.get_config(None)
        ch2_client = self

        class AutotesterClient(discord.Client):
            async def on_ready(self):
                channel = await self.fetch_channel(
                    config.end_to_end_test_discord_channel_id
                )
                await channel.send("Hello")
                ch2_client.pending_shutdown = True

        client = AutotesterClient(intents=discord.Intents.default())
        run_task(client.start(config.end_to_end_test_discord_token))


def is_continue_command(message_content: str):
    return message_content.strip() == "/continue" or message_content.startswith(
        "m continue"
    )


def is_mu_command(message_content: str):
    return message_content.strip() == "/mu" or message_content.startswith("m mu")


def realize_pings(channel: discord.TextChannel, message_content: str):
    for member in channel.members:
        if "@" + member.name in message_content:
            message_content = message_content.replace(
                "@" + member.name, f"<@!{member.id}>"
            )
    return message_content


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
) -> str | None:
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
