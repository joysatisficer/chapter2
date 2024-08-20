import asyncio
import contextlib
import re
import time
import urllib.parse
import random
from typing import Self, Tuple

import discord
import discord.http
import discord.threads
import openai.error
import yaml
from pydantic import ValidationError

import ontology
from interfaces.deserves_reply import deserves_reply
from util.asyncutil import async_generator_to_reusable_async_iterable, run_task
from util.discord_improved import ScheduleTyping, parse_discord_content
from declarations import GenerateResponse, Message, UserID, Author, JSON, ActionHistory
from ontology import Config, DiscordInterfaceConfig


class DiscordInterface(discord.Client):
    MAX_CONCURRENT_MESSAGES = 100_000

    def __init__(
        self,
        base_config: Config,
        generate_response: GenerateResponse,
        em_name: str,
        iface_config: DiscordInterfaceConfig,
    ):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        if (
            iface_config.discord_proxy_url is None
            or not iface_config.discord_proxy_url.startswith("http")
        ):
            super().__init__(intents=intents)
        else:
            super().__init__(intents=intents, proxy=iface_config.discord_proxy_url)
        self.base_config: Config = base_config
        self.generate_response: GenerateResponse = generate_response
        self.em_name = em_name
        self.iface_config = iface_config
        self.message_semaphore = asyncio.BoundedSemaphore(self.MAX_CONCURRENT_MESSAGES)
        self.per_interlocutor_semaphore: dict[int, asyncio.Semaphore] = {}
        self.pending_shutdown = False
        if (
            self.iface_config.discord_proxy_url is not None
            and self.iface_config.discord_proxy_url.startswith("socks")
        ):
            from aiohttp_socks import ProxyConnector
            from discord.state import ConnectionState

            self.http = discord.http.HTTPClient(
                self.loop, ProxyConnector.from_url(iface_config.discord_proxy_url)
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
                elif re.match("^[.,][^\s.,]", this_message.content):
                    pass
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
                config, iface_config = await self.get_config(message.channel)
            except (ValueError, ValidationError) as exc:
                raise ConfigError() from exc
            # XXX: Relies on Discord for IDs
            if message.author.id not in self.per_interlocutor_semaphore:
                # XXX: Might not be thread-safe
                # XXX: This is not garbage-collected
                self.per_interlocutor_semaphore[message.author.id] = asyncio.Semaphore()
            if (
                len(self.per_interlocutor_semaphore[message.author.id]._waiters or [])
                > iface_config.max_queued_replies
            ) and command_message is None:
                return
            async with self.per_interlocutor_semaphore[message.author.id]:
                try:
                    my_user_id = UserID(str(self.user.id), "discord")

                    async def message_history():
                        nonlocal message
                        async for this_message in message.channel.history(limit=None):
                            if is_continue_command(this_message.content):
                                pass
                            elif iface_config.ignore_dotted_messages and re.match(
                                "^[.,][^\s.,]", this_message.content
                            ):
                                pass
                            else:
                                yield await self.discord_message_to_message(
                                    config, this_message
                                )
                        if iface_config.threads_inherit_history and isinstance(
                            message.channel, discord.threads.Thread
                        ):
                            thread = message.channel
                            # starter message id is the same as the thread id if the
                            # thread is attached to a message
                            if message.channel.starter_message is not None:
                                starter_message_id = thread.id
                            elif message.channel.name.startswith("past:"):
                                starter_message_id = message.channel.name.split(
                                    "past:"
                                )[1]
                            else:
                                return
                            # TODO(perf): try to get from cache first
                            starter_message = (
                                await message.channel.parent.fetch_message(
                                    starter_message_id
                                )
                            )
                            if starter_message is not None:
                                async for (
                                    this_message
                                ) in starter_message.channel.history(
                                    limit=None, before=starter_message
                                ):
                                    if is_continue_command(this_message.content):
                                        pass
                                    elif (
                                        iface_config.ignore_dotted_messages
                                        and re.match(
                                            "^[.,][^\s.,]", this_message.content
                                        )
                                    ):
                                        pass
                                    else:
                                        yield await self.discord_message_to_message(
                                            config, this_message
                                        )

                    if not await self.should_reply(
                        message,
                        config,
                        iface_config,
                        my_user_id,
                        async_generator_to_reusable_async_iterable(message_history),
                    ):
                        return
                    response_messages = self.generate_response(
                        my_user_id,
                        async_generator_to_reusable_async_iterable(message_history),
                        config.em,
                    )
                    async with ScheduleTyping(
                        message.channel, typing=iface_config.send_typing
                    ):
                        first_message = True
                        async for reply_message in response_messages:
                            if (
                                reply_message.author.user_id == my_user_id
                                and not isempty(reply_message.content)
                            ):
                                # send a new typing event if it's not the first message
                                if not first_message:
                                    run_task(
                                        message._state.http.send_typing(
                                            message.channel.id
                                        )
                                    )
                                await wait_until_timestamp(
                                    reply_message.timestamp, message.channel.typing
                                )
                                await message.channel.send(
                                    await realize_pings(
                                        self, message.channel, reply_message.content
                                    )
                                )
                                first_message = False
                finally:
                    if command_message is not None:
                        await command_message.delete()

    async def discord_message_to_message(
        self, config, message: discord.Message
    ) -> Message:
        if message.author.id == self.user.id:
            author_name = config.em.name
        else:
            author_name = message.author.name
        return Message(
            Author(author_name, UserID(str(message.author.id), "discord")),
            await parse_discord_content(message, self.user.id, config.em.name),
            message.created_at.timestamp(),
        )

    async def should_reply(
        self,
        message: discord.Message,
        config: Config,
        iface_config: DiscordInterfaceConfig,
        user_id: UserID,
        message_history: ActionHistory,
    ) -> bool:
        return (
            message.author != self.user
            and (
                not isinstance(message.channel, discord.abc.GuildChannel)
                or message.channel.permissions_for(message.guild.me).send_messages
            )
            and not (
                iface_config.mute is True
                or iface_config.mute == self.em_name
                or (
                    isinstance(iface_config.mute, list)
                    and self.em_name in iface_config.mute
                )
            )
            and not (
                iface_config.thread_mute
                and message.channel.type == discord.ChannelType.public_thread
            )
            and not (
                iface_config.ignore_dotted_messages
                and re.match("^[.,][^\s.,]", message.content)
            )
            and (
                len(iface_config.discord_user_whitelist) == 0
                or message.author.id in iface_config.discord_user_whitelist
            )
            and (
                (iface_config.reply_on_ping and self.user.mentioned_in(message))
                or (
                    iface_config.reply_on_random
                    and random.random() < (1 / iface_config.reply_on_random)
                )
                or (
                    # first or last four names
                    iface_config.reply_on_name
                    and any(
                        re.match(
                            r"^([^\s]+\b){0,3}" + re.escape(name),
                            message.content,
                            re.IGNORECASE,
                        )
                        or re.search(
                            re.escape(name) + r"([^\s]+\b){0,3}$",
                            message.content,
                            re.IGNORECASE,
                        )
                        for name in (
                            config.em.name,
                            self.em_name,
                            self.user.name,
                            *iface_config.nicknames,
                        )
                    )
                )
                or (
                    iface_config.reply_on_sim
                    and await deserves_reply(
                        self.generate_response,
                        config,
                        user_id,
                        message_history,
                        iface_config.reply_on_sim,
                    )
                )
            )
        )

    async def get_config(
        self, channel: "discord.abc.MessageableChannel"
    ) -> Tuple[Config, DiscordInterfaceConfig]:
        if isinstance(channel, dict):
            kv = channel
        elif channel is not None:
            kv = await get_yaml_from_channel(channel)
        else:
            kv = {}
        config = ontology.load_config_from_kv(kv, self.base_config.model_dump())
        iface_config = DiscordInterfaceConfig(
            **ontology.transpose_keys(
                ontology.overlay(kv, {"interfaces": [self.iface_config.model_dump()]})
            )["interfaces"][0]
        )
        return config, iface_config

    @contextlib.asynccontextmanager
    async def handle_exceptions(self, message: discord.Message):
        config, iface_config = await self.get_config(None)
        try:
            async with self.message_semaphore:
                yield None
        except ConfigError as exc:
            if iface_config.end_to_end_test:
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
            import os, asyncio, fire, selectors
            from rich.console import Console

            if iface_config.end_to_end_test:
                self.end_to_end_test_fail = True
            await message.add_reaction("⚠")
            if isinstance(exc, ConnectionError):
                await message.add_reaction("📵")
            if isinstance(exc, openai.error.APIConnectionError):
                await message.add_reaction("🌩️")
            print(
                "exception in channel",
                format_cli_link(
                    message.jump_url,
                    f"#{message.channel.name}",
                ),
            )
            if "PYCHARM_HOSTED" not in os.environ:
                Console().print_exception(
                    suppress=(asyncio, fire, selectors), show_locals=True
                )
            else:
                import traceback

                traceback.print_exc()
            raise
        finally:
            if (
                self.pending_shutdown
                and self.message_semaphore._value == self.MAX_CONCURRENT_MESSAGES
            ):
                await self.close()

    async def on_ready(self):
        print(f"Invite the bot: {self.get_invite_link()}")
        print("Discord interface ready")
        if self.iface_config.end_to_end_test:
            run_task(self.end_to_end_test())

    def get_invite_link(self):
        if self.user.id is None:
            raise ValueError("Tried to get invite link before bot user ID is known")
        return "https://discord.com/api/oauth2/authorize?" + urllib.parse.urlencode(
            {"scope": "bot", "permissions": 536879168, "client_id": self.user.id}
        )

    async def start(self, token: str = None, *args, **kwargs) -> None:
        if token is None:
            token = self.iface_config.discord_token
        return await super().start(token, *args, **kwargs)

    def stop(self, sig, frame):
        self.pending_shutdown = True
        asyncio.create_task(self.handle_shutdown())

    async def handle_shutdown(self):
        if self.message_semaphore._value == self.MAX_CONCURRENT_MESSAGES:
            await self.close()
        self.pending_shutdown = True

    async def end_to_end_test(self):
        config, iface_config = await self.get_config(None)
        ch2_client = self

        class AutotesterClient(discord.Client):
            async def on_ready(self):
                channel = await self.fetch_channel(
                    iface_config.end_to_end_test_discord_channel_id
                )
                await channel.send("Hello")
                ch2_client.pending_shutdown = True

        client = AutotesterClient(intents=discord.Intents.default())
        run_task(client.start(iface_config.end_to_end_test_discord_token))


def is_continue_command(message_content: str):
    return message_content.strip() == "/continue" or message_content.startswith(
        "m continue"
    )


def is_mu_command(message_content: str):
    return message_content.strip() == "/mu" or message_content.startswith("m mu")


async def realize_pings(self, channel: discord.TextChannel, message_content: str):
    if isinstance(channel, discord.DMChannel):
        members = [channel.recipient]
    elif isinstance(channel, discord.Thread):
        members = []
        if channel.parent is not None:
            members = channel.parent.members
        else:
            for member in await channel.fetch_members():
                members.append(await self.fetch_user(member.id))
    else:
        members = channel.members
    for member in members:
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
