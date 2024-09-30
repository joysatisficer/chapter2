import asyncio
import contextlib
import re
import time
import random
from typing import Self, Tuple
from collections import defaultdict

import discord
import discord.http
import discord.threads
import openai.error
import yaml
import requests
import ast
from pydantic import ValidationError

import ontology
from message_formats import hashint
from trace import trace, ot_tracer, log_trace_id_to_console
from interfaces.deserves_reply import deserves_reply
from util.asyncutil import async_generator_to_reusable_async_iterable, run_task
from util.discord_improved import ScheduleTyping, parse_discord_content
from declarations import GenerateResponse, Message, UserID, Author, JSON, ActionHistory
from ontology import Config, DiscordInterfaceConfig


class DiscordInterface(discord.Client):
    DOTTED_MESSAGE_RE = r"^[.,][^\s.,]"
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
            or not iface_config.discord_proxy_url.get_secret_value().startswith("http")
        ):
            super().__init__(intents=intents)
        else:
            super().__init__(
                intents=intents, proxy=iface_config.discord_proxy_url.get_secret_value()
            )
        self.base_config: Config = base_config
        self.generate_response: GenerateResponse = generate_response
        self.sysname = em_name
        self.iface_config = iface_config
        self.message_semaphore = asyncio.BoundedSemaphore(self.MAX_CONCURRENT_MESSAGES)
        self.per_interlocutor_semaphore: dict[int, asyncio.Semaphore] = defaultdict(
            asyncio.Semaphore
        )
        self.pinned_yaml: dict[int, dict] = {}
        self.pending_shutdown = False
        if (
            self.iface_config.discord_proxy_url is not None
            and self.iface_config.discord_proxy_url.get_secret_value().startswith(
                "socks"
            )
        ):
            from aiohttp_socks import ProxyConnector
            from discord.state import ConnectionState

            self.http = discord.http.HTTPClient(
                self.loop,
                ProxyConnector.from_url(
                    iface_config.discord_proxy_url.get_secret_value()
                ),
            )
            self._connection: ConnectionState[Self] = self._get_state(intents=intents)
            self._connection.shard_count = self.shard_count
            self._connection._get_websocket = self._get_websocket
            self._connection._get_client = lambda: self

    async def on_message(self, message: discord.Message) -> None:
        if is_continue_command(message.content):
            if not self.user.mentioned_in(message):
                return
            command_message = message
            message_to_react_to = [
                message async for message in message.channel.history(limit=2)
            ][1]
        elif is_mu_command(message.content):
            if not self.user.mentioned_in(message):
                return
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
            # XXX: Might not be thread-safe
            # XXX: This is not garbage-collected
            if (
                len(self.per_interlocutor_semaphore[message.author.id]._waiters or [])
                > iface_config.max_queued_replies
            ) and command_message is None:
                return
            async with self.per_interlocutor_semaphore[message.author.id]:
                try:
                    my_user_id = UserID(str(self.user.id), "discord")
                    message_ids = set()
                    hash_to_id = None

                    @trace
                    async def message_history(message, first_message=None):
                        message_ids.add(message.id)
                        yield self.discord_message_to_message(
                            config, iface_config, message
                        )
                        async for this_message in message.channel.history(
                            limit=None, before=message, after=first_message
                        ):
                            if is_continue_command(this_message.content):
                                pass
                            elif is_mu_command(this_message.content):
                                pass
                            elif (
                                this_message.type
                                == discord.MessageType.thread_starter_message
                            ):
                                pass
                            elif this_message.type == discord.MessageType.pins_add:
                                pass
                            elif iface_config.ignore_dotted_messages and re.match(
                                self.DOTTED_MESSAGE_RE, this_message.content
                            ):
                                pass
                            else:
                                message_ids.add(this_message.id)
                                yield self.discord_message_to_message(
                                    config, iface_config, this_message
                                )
                            if this_message.content.startswith(".history\n"):
                                content_after_delimiter = this_message.content.split(
                                    "---", 1
                                )[-1]
                                param_dict = (
                                    yaml.safe_load(content_after_delimiter) or {}
                                )
                                if "last" in param_dict:
                                    last = await self.get_message_from_link(
                                        param_dict["last"]
                                    )
                                    first = None
                                    if "first" in param_dict:
                                        first = await self.get_message_from_link(
                                            param_dict["first"]
                                        )
                                    if last is not None:
                                        async for msg in message_history(last, first):
                                            yield msg
                                if (
                                    "passthrough" not in param_dict
                                    or param_dict["passthrough"] is False
                                ):
                                    return
                        if first_message is not None:
                            message_ids.add(first_message.id)
                            yield self.discord_message_to_message(
                                config, iface_config, first_message
                            )
                        elif iface_config.threads_inherit_history and isinstance(
                            message.channel, discord.threads.Thread
                        ):
                            thread = message.channel
                            # starter message id is the same as the thread id if the
                            # thread is attached to a message
                            if message.channel.name.startswith("new:"):
                                return
                            elif message.channel.name.startswith("past:"):
                                starter_message_id = message.channel.name.split(
                                    "past:"
                                )[1]
                            elif thread.id is not None:
                                starter_message_id = thread.id
                            else:
                                return
                            starter_message = (
                                await message.channel.parent.fetch_message(
                                    starter_message_id
                                )
                            )
                            if starter_message is not None:
                                async for msg in message_history(starter_message):
                                    yield msg

                    if not await self.should_reply(
                        message,
                        config,
                        iface_config,
                        my_user_id,
                        async_generator_to_reusable_async_iterable(
                            message_history, message
                        ),
                    ):
                        return
                    response_messages = self.generate_response(
                        my_user_id,
                        async_generator_to_reusable_async_iterable(
                            message_history, message
                        ),
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
                                if reply_message.content.isspace():
                                    continue
                                content = reply_message.content
                                reference = None
                                # todo: move parsing inside message format
                                if match := re.match(
                                    r"^(.*)\s?\[reply:([0-9a-f]+)]\s?(.*)",
                                    reply_message.content,
                                ):
                                    idhash = match.group(2)
                                    content = match.group(1) + match.group(3)
                                    if hash_to_id is None:
                                        hash_to_id = {
                                            hashint(message_id): message_id
                                            for message_id in message_ids
                                        }
                                    if ref_id := hash_to_id.get(idhash):
                                        reference = discord.MessageReference(
                                            message_id=ref_id,
                                            channel_id=message.channel.id,
                                            guild_id=message.guild.id,
                                        )
                                await message.channel.send(
                                    await realize_pings(self, message.channel, content),
                                    reference=reference,
                                )
                                trace.send_message(reply_message.content)
                                first_message = False
                finally:
                    if command_message is not None:
                        await command_message.delete()

    def discord_message_to_message(
        self, config, iface_config: DiscordInterfaceConfig, message: discord.Message
    ) -> Message:
        if message.author.id == self.user.id:
            author_name = config.em.name
        else:
            author_name = message.author.name
        content = parse_discord_content(message, self.user.id, config.em.name)
        if iface_config.include_images:
            for attachment in message.attachments:
                if attachment.content_type.startswith(
                    "text/"
                ) and not attachment.filename.startswith("config"):
                    attachment_content = get_attachment_content(attachment)
                    content += f"<|begin_of_text_attachment|>{attachment_content}<|end_of_text_attachment|>"
                    continue
                if attachment.width is None or attachment.height is None:
                    continue
                else:
                    if (
                        attachment.width > iface_config.image_limits.max_width
                        or attachment.height > iface_config.image_limits.max_height
                    ):
                        width_ratio = (
                            iface_config.image_limits.max_width / attachment.width
                        )
                        height_ratio = (
                            iface_config.image_limits.max_height / attachment.height
                        )
                        scale_factor = min(width_ratio, height_ratio)
                        width = int(attachment.width * scale_factor)
                        height = int(attachment.height * scale_factor)
                        url = (
                            attachment.proxy_url.rstrip("&")
                            + f"&width={width}&height={height}"
                        )
                    else:
                        url = attachment.proxy_url
                    content += f"<|begin_of_img_url|>{url}<|end_of_img_url|>"
        return Message(
            Author(author_name, UserID(str(message.author.id), "discord")),
            content,
            timestamp=message.created_at.timestamp(),
            id=hashint(message.id),
            reply_to=message.reference
            and message.reference.message_id
            and hashint(message.reference.message_id),
        )

    @trace
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
                or iface_config.mute == self.sysname
                or (
                    isinstance(iface_config.mute, list)
                    and any(
                        name in iface_config.mute
                        for name in (config.em.name, self.sysname, self.user.name)
                    )
                )
            )
            and not (
                iface_config.thread_mute
                and message.channel.type == discord.ChannelType.public_thread
            )
            and not (
                iface_config.ignore_dotted_messages
                and re.match(self.DOTTED_MESSAGE_RE, message.content)
            )
            and (
                len(iface_config.discord_user_whitelist) == 0
                or message.author.id in iface_config.discord_user_whitelist
            )
            and (
                len(iface_config.may_speak) == 0
                or any(
                    name in iface_config.may_speak
                    for name in (config.em.name, self.sysname, self.user.name)
                )
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
            if channel.id not in self.pinned_yaml:
                await self.update_pinned_yaml(channel)
            kv = get_yaml_from_channel(channel) | self.pinned_yaml[channel.id]
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
        with ot_tracer.start_as_current_span(self.handle_exceptions.__qualname__):
            try:
                async with self.message_semaphore:
                    yield
            except ConfigError as exc:
                if iface_config.end_to_end_test:
                    self.end_to_end_test_fail = True
                await message.add_reaction("⚙️")
                # if the message is deleted, the url will still head to the channel
                print(
                    "bad config in channel",
                    f"#{message.channel.name}",
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
                print("exception in channel", f"#{message.channel.name}")
                if "PYCHARM_HOSTED" not in os.environ:
                    Console().print_exception(
                        suppress=(asyncio, fire, selectors), show_locals=True
                    )
                else:
                    import traceback

                    traceback.print_exc()
                log_trace_id_to_console()
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
        return discord.utils.oauth_url(
            self.user.id,
            scopes=["bot"],
            permissions=discord.Permissions(
                add_reactions=True,
                manage_messages=True,
                manage_webhooks=True,
            ),
        )

    @trace
    async def start(self, token: str = None, *args, **kwargs) -> None:
        if token is None:
            token = self.iface_config.discord_token.get_secret_value()
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
                # channel = await self.fetch_channel(
                #     iface_config.end_to_end_test_discord_channel_id
                # )
                channel = await self.get_channel_cached(
                    iface_config.end_to_end_test_discord_channel_id
                )
                await channel.send("Hello")
                ch2_client.pending_shutdown = True

        client = AutotesterClient(intents=discord.Intents.default())
        run_task(client.start(iface_config.end_to_end_test_discord_token))

    async def get_channel_cached(self, channel_id: str):
        return self.get_channel(channel_id) or await self.fetch_channel(channel_id)

    async def get_message_from_link(
        self,
        message_link: str,
    ):
        message_id = message_link.split("/")[-1]
        channel_id = message_link.split("/")[-2]
        if channel_id is not None and message_id is not None:
            thread = await self.get_channel_cached(channel_id)
            return await thread.fetch_message(message_id)
        else:
            return None

    async def update_pinned_yaml(self, channel):
        self.pinned_yaml[channel.id] = await get_yaml_from_pinned_messages(
            channel, self.user.name
        )

    async def on_guild_channel_pins_update(self, channel, _last_pin):
        await self.update_pinned_yaml(channel)

    async def on_private_channel_pins_update(self, channel, _last_pin):
        await self.update_pinned_yaml(channel)


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


def get_yaml_from_channel(
    channel: "discord.abc.MessageableChannel",
) -> JSON:
    topic = get_channel_topic(channel)
    if topic is not None and "---" in topic:
        return yaml.safe_load(topic.split("---")[1]) or {}
    else:
        return {}


async def get_yaml_from_pinned_messages(
    channel: "discord.abc.MessageableChannel",
    em_name: str,
):
    pinned_messages = await channel.pins()
    config_prefix = f".config\n"
    em_config_prefix = f".config.{em_name}\n"
    config_filename_prefix = f"config.yaml"
    em_config_filename_prefix = f"config.{em_name}.yaml"

    valid_configs = []

    for message in pinned_messages:
        if message.content.startswith(
            (
                config_prefix,
                em_config_prefix,
            )
        ):
            content_after_delimiter = message.content.split("---", 1)[-1]
            valid_configs.append(content_after_delimiter)
        if message.attachments:
            for attachment in message.attachments:
                if attachment.filename.startswith(
                    (
                        config_filename_prefix,
                        em_config_filename_prefix,
                    )
                ):
                    attachment_content = get_attachment_content(attachment)
                    valid_configs.append(attachment_content)

    if not valid_configs:
        return {}

    config = {}
    for config_content in reversed(valid_configs):
        d = yaml.safe_load(config_content) or {}
        config.update(d)
    return config


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


def get_attachment_content(attachment: discord.Attachment):
    r = requests.get(attachment.url, allow_redirects=True)
    attachment_content = r.content
    decoded_content = attachment_content.decode("utf-8")  # Assuming UTF-8 encoding
    unescaped_content = unescape_string(decoded_content)
    return unescaped_content


def unescape_string(escaped_string: str) -> str:
    try:
        # Use ast.literal_eval to safely evaluate the string
        return ast.literal_eval(f"'''{escaped_string}'''")
    except (SyntaxError, ValueError):
        # If there's an error, return the original string
        return escaped_string


class ConfigError(ValueError):
    pass
