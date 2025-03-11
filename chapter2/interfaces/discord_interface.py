import asyncio
import contextlib
import re
import textwrap
import time
import random
from datetime import datetime
from typing import Self, Tuple, Optional, Union, AsyncIterator
from collections.abc import Callable
from collections import defaultdict
from functools import lru_cache

import aiohttp
from faculties.history_faculty import transcript_to_messages
import discord
import discord.http
import discord.threads
import yaml
import requests
import ast
from pydantic import ValidationError
from sortedcontainers import SortedDict
from asgiref.sync import sync_to_async
from aioitertools.more_itertools import take as async_take
from io import StringIO


import ontology
from faculties.contrib.airtable_notes_faculty import get_airtable
from message_formats import hashint
from trace import trace, ot_tracer, log_trace_id_to_console
from interfaces.deserves_reply import deserves_reply
from util.asyncutil import async_generator_to_reusable_async_iterable, run_task
from util.discord_improved import ScheduleTyping, parse_discord_content, resolve_member
from declarations import GenerateResponse, Message, Author, JSON, ActionHistory
from ontology import Config, DiscordInterfaceConfig, HistoryFacultyConfig


class DiscordInterface(discord.Client):
    """Stability: Gold"""

    MAX_CONCURRENT_MESSAGES = 100_000
    DOTTED_MESSAGE_RE = r"^[.,][^\s.,]"

    def __init__(
        self,
        base_config: Config,
        generate_response: GenerateResponse,
        em_name: str,
        iface_config: DiscordInterfaceConfig,
    ):
        intents = discord.Intents.default()
        intents.typing = False
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
        self.pinned_messages: defaultdict[int, set[int]] = defaultdict(set)
        self.pins: dict[int, list[discord.Message]] = {}
        self.cache = Cache()
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

    # @trace
    async def message_history(
        self,
        message: discord.Message,
        first_message: Optional[discord.Message] = None,
        config: Optional[Config] = None,
        iface_config: Optional[DiscordInterfaceConfig] = None,
        pov_user_id: Optional[int] = None,
        inclusive: bool = True,
    ):
        if inclusive and message and not self.message_invisible(message, iface_config):
            yield await self.discord_message_to_message(
                config, iface_config, message, pov_user_id
            )

        async for this_message in self.cache(message.channel).history(
            limit=None,
            before=message,
            after=first_message,
        ):
            if not message or self.message_invisible(this_message, iface_config):
                pass
            else:
                yield await self.discord_message_to_message(
                    config, iface_config, this_message, pov_user_id
                )
            config_message = self.parse_dot_command(this_message)
            if config_message and config_message["command"] == "history":
                if self.config_applies_to_user(this_message, config, pov_user_id):
                    first_link, last_link, _, passthrough = self.parse_history_config(
                        config_message["yaml"]
                    )
                    if last_link is not None:
                        last = await self.get_message_from_link(last_link)
                        first = None
                        if first_link is not None:
                            first = await self.get_message_from_link(first_link)
                        if last is not None:
                            if first is None:
                                first = first_message
                            async for msg in self.message_history(
                                last, first, config, iface_config, pov_user_id
                            ):
                                yield msg
                    for attachment in this_message.attachments:
                        att_data = await self.parse_attachment(attachment)
                        if att_data and att_data["type"] == "text":
                            transcript = att_data["content"]
                            if transcript:
                                config_params = {
                                    k: v
                                    for k, v in config_message["yaml"].items()
                                    if k in ["nickname", "nicknames", "input_format"]
                                }
                                async for msg in transcript_to_messages(
                                    transcript,
                                    HistoryFacultyConfig(**config_params),
                                    config.em,
                                ):
                                    yield (msg, frozenset())
                    if not passthrough:
                        return
        if first_message is not None:
            yield await self.discord_message_to_message(
                config, iface_config, first_message, pov_user_id
            )
        elif iface_config.threads_inherit_history and isinstance(
            message.channel, discord.threads.Thread
        ):
            starter_message = await self.get_starter_message(message)
            if starter_message is not None:
                async for msg in self.message_history(
                    starter_message,
                    first_message=None,
                    config=config,
                    iface_config=iface_config,
                    pov_user_id=pov_user_id,
                ):
                    yield msg

    async def get_starter_message(self, message: discord.Message):
        if not isinstance(message.channel, discord.threads.Thread):
            return None
        thread = message.channel
        # starter message id is the same as the thread id if the
        # thread is attached to a message
        if message.channel.name.startswith("new:"):
            return None
        elif message.channel.name.startswith("past:"):
            starter_message_id = message.channel.name.split("past:")[1]
        elif thread.id is not None:
            starter_message_id = thread.id
        else:
            return None
        try:
            starter_message = await message.channel.parent.fetch_message(
                starter_message_id
            )
        except discord.errors.NotFound:
            starter_message = None
        return starter_message

    async def on_message(self, message: discord.Message) -> None:

        self.cache(message.channel).update(message, True)

        if is_command := is_continue_command(message.content):
            if not self.user.mentioned_in(message):
                return
        elif is_command := is_mu_command(message.content):
            if not self.user.mentioned_in(message):
                return
            async for this_message in self.cache(message.channel).history(
                before=message
            ):
                if this_message.author.id == self.user.id:
                    await this_message.delete()
                elif re.match("^[.,][^\s.,]", this_message.content):
                    pass
                else:
                    break

        async with self.handle_exceptions(
            (
                # cache misses are unlikely here
                await anext(
                    self.cache(message.channel).history(limit=1, before=message)
                )
                if is_command
                else message
            )
        ):
            try:
                config, iface_config = await self.get_config(message.channel)
            except (ValueError, ValidationError) as exc:
                if is_command:
                    await message.delete()
                raise ConfigError() from exc
            # XXX: Relies on Discord for IDs
            # XXX: Might not be thread-safe
            # XXX: This is not garbage-collected
            if (
                len(self.per_interlocutor_semaphore[message.author.id]._waiters or [])
                > iface_config.max_queued_replies
            ) and not is_command:
                return
            async with self.per_interlocutor_semaphore[message.author.id]:
                try:
                    await self.handle_reply(message, config, iface_config)
                finally:
                    if is_command:
                        await message.delete()

    async def handle_reply(
        self,
        message: discord.Message,
        config: Config,
        iface_config: DiscordInterfaceConfig,
        webhook: Optional[discord.Webhook] = None,
        pov_user_id: Optional[int] = None,
    ):
        @trace
        def message_history(
            message,
            first_message=None,
            config=config,
            iface_config=iface_config,
            pov_user_id=pov_user_id if pov_user_id is not None else self.user.id,
        ):
            return self.message_history(
                message,
                first_message,
                config,
                iface_config,
                pov_user_id,
            )

        if not await self.should_reply(
            self.user,
            message,
            config,
            iface_config,
            async_generator_to_reusable_async_iterable(
                lambda: (message async for message, _ in message_history(message))
            ),
        ):
            return

        history, raw_mentions = zip(
            *(
                await async_take(
                    config.em.recency_window,
                    async_generator_to_reusable_async_iterable(
                        message_history, message
                    ),
                )
            )
        )

        mentions = set()
        for these_mentions in raw_mentions:
            mentions.update(these_mentions)

        response_messages = self.generate_response(
            config.em,
            history,
        )

        async def send_to_channel(**kwargs):
            if webhook is not None:
                return await self.webhook_send(
                    webhook,
                    message.channel,
                    username=config.em.name,
                    avatar_url=iface_config.avatar_url,
                    **kwargs,
                )
                # return await webhook.send(
                #     username=config.em.name,
                #     avatar_url=iface_config.avatar_url,
                #     thread=(
                #         message.channel
                #         if isinstance(message.channel, discord.Thread)
                #         else None
                #     ),
                #     **kwargs,
                # )
            else:
                return await message.channel.send(**kwargs)

        async with ScheduleTyping(message.channel, typing=iface_config.send_typing):
            first_message = True
            async for reply_message in response_messages:
                if reply_message.author.name == "reasoning_content" and not isempty(
                    reply_message.content
                ):
                    prefix = (
                        ".:thought_balloon:" if config.em.reasoning["hidden"] else ""
                    )
                    start_token = config.em.reasoning["start_token"]
                    end_token = config.em.reasoning["end_token"]
                    inner_content = (
                        f"{start_token}\n{reply_message.content.strip()}\n{end_token}"
                    )
                    if len(reply_message.content) > 1900:
                        attachment = discord.File(
                            StringIO(inner_content),
                            filename=f"reasoning.txt",
                        )
                        await send_to_channel(content=prefix, file=attachment)
                    else:
                        await send_to_channel(
                            content=prefix + f"\n```{inner_content}```"
                        )
                    first_message = False
                elif reply_message.author.name == config.em.name and not isempty(
                    reply_message.content
                ):
                    # send a new typing event if it's not the first message
                    if not first_message:
                        run_task(message._state.http.send_typing(message.channel.id))
                    await wait_until_timestamp(
                        reply_message.timestamp, message.channel.typing
                    )
                    if reply_message.content.isspace():
                        continue
                    content = reply_message.content
                    await send_to_channel(content=self.realize_pings(content, mentions))
                    if iface_config.exo_enabled:
                        await self.respond_to_tools(message.channel, reply_message)
                    trace.send_message(reply_message.content)
                    first_message = False

    async def respond_to_tools(self, channel, reply_message: Message):
        if reply_message.content.startswith("exo create_note "):
            note_content = (
                reply_message.content.removeprefix("exo create_note ")
                .removeprefix('"')
                .removesuffix('"')
            )
            record = await sync_to_async(
                get_airtable(self.iface_config.airtable).create,
                thread_sensitive=False,
            )({"Note": note_content})
            webhook = await self.get_my_webhook_for_channel(channel)
            await webhook.send(
                textwrap.dedent(
                    f"""\
            exOS Chapter II
            ---
            Command: exo create_note "{note_content}"
            Time: {datetime.now():%m/%d/%Y, %I:%M:%S %p}
            ---

            Note created. The 'exo notes' faculty shows all your personal notes

            Your note has been created with ID: {record["id"]}

            ---
            Type 'help' for available commands."""
                ),
                username="exOS",
                allowed_mentions=discord.AllowedMentions.none(),
            )
        elif reply_message.content == "help":
            webhook = await self.get_my_webhook_for_channel(channel)
            await webhook.send(
                textwrap.dedent(
                    f"""\
            exOS Chapter II
            ---
            Command: help
            Time: {datetime.now():%m/%d/%Y, %I:%M:%S %p}
            ---
            
            Global Help
            
            Available environments: exo
            Use "<environment> help" for environment-specific commands.
            """
                ),
                username="exOS",
                allowed_mentions=discord.AllowedMentions.none(),
            )
        elif reply_message.content == "exo help":
            webhook = await self.get_my_webhook_for_channel(channel)
            await webhook.send(
                textwrap.dedent(
                    f"""\
            exOS Chapter II
            ---
            Command: help
            Time: {datetime.now():%m/%d/%Y, %I:%M:%S %p}
            ---

            Exo Help
            
            Available commands:
            create_note <note_string> - Create a new note

            ---
            Type 'help' for available commands.
            """
                ),
                username="exOS",
                allowed_mentions=discord.AllowedMentions.none(),
            )

    async def discord_message_to_message(
        self,
        config,
        iface_config: DiscordInterfaceConfig,
        message: discord.Message,
        # pov_user: Optional[discord.User] = None,
        pov_user_id: Optional[int] = None,
    ) -> Tuple[Message, frozenset[Union[discord.User, discord.Member]]]:
        # if pov_user is None:
        #     pov_user = self.user

        # use self id parameter instead of pov_user
        if message.author.id == pov_user_id:
            author_name = config.em.name
        else:
            author_name = message.author.name
        content = parse_discord_content(message, pov_user_id, config.em.name)
        if message.reference is not None and not message.is_system():  # is it a reply?
            if message.reference.resolved is not None:
                referenced_message = message.reference.resolved
            elif message.reference.cached_message is not None:
                referenced_message = message.reference.cached_message
            else:
                try:
                    referenced_message = await message.channel.fetch_message(
                        message.reference.message_id
                    )
                except discord.NotFound:
                    referenced_message = None
            if referenced_message is None or isinstance(
                referenced_message, discord.DeletedReferencedMessage
            ):
                prefix = "@deleted-message "
            else:
                prefix = (
                    resolve_member(
                        message,
                        referenced_message.author.id,
                        pov_user_id,
                        config.em.name,
                    )
                    + " "
                )
            content = prefix + content
        for attachment in message.attachments:
            att_data = await self.parse_attachment(attachment)
            if iface_config.ignore_dotted_messages and (
                att_data["command"] in ["config", "history"]
                or re.match(self.DOTTED_MESSAGE_RE, attachment.filename)
            ):
                continue
            if att_data["type"] == "text":
                if attachment.filename == "reasoning.txt":
                    att_begin_token = ""
                    att_end_token = ""
                else:
                    att_begin_token = f"<attachment filename={attachment.filename}>\n"
                    att_end_token = "\n</attachment>"
                att_content = (await self.get_attachment_content(attachment)).rstrip()
                content += f"\n{att_begin_token}{att_content}{att_end_token}"
            elif iface_config.include_images and att_data["type"] == "image":
                if (
                    attachment.width > iface_config.image_limits.max_width
                    or attachment.height > iface_config.image_limits.max_height
                ):
                    width_ratio = iface_config.image_limits.max_width / attachment.width
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
        channel = message.channel

        if message.reference and content == "":
            # hacky check for forwarded message; discord.py version 2.5 has a type for it but that doesnt seem to be out yet
            if (
                message.reference.channel_id is not None
                and message.reference.message_id is not None
            ):
                thread = await self.get_channel_cached(message.reference.channel_id)
                forwarded_message = await thread.fetch_message(
                    message.reference.message_id
                )
                if forwarded_message:
                    content = f"<|begin_of_fwd|>{parse_discord_content(forwarded_message, pov_user_id, config.em.name)}<|end_of_fwd|>"
        return Message(
            Author(author_name),
            content.strip(),
            timestamp=message.created_at.timestamp(),
            id=hashint(message.id),
            reply_to=message.reference
            and message.reference.message_id
            and hashint(message.reference.message_id),
        ), frozenset(
            (channel.me, channel.recipient)
            if isinstance(channel, discord.DMChannel)
            else (message.mentions + [message.author])
        )

    @trace
    async def get_config(
        self,
        channel: Optional["discord.abc.MessageableChannel"] = None,
        base_config: Optional[Config] = None,
        base_iface_config: Optional[DiscordInterfaceConfig] = None,
        pov_user: Optional[discord.User] = None,
    ) -> Tuple[Config, DiscordInterfaceConfig]:
        if base_config is None:
            base_config = self.base_config
        if base_iface_config is None:
            base_iface_config = self.iface_config
        if pov_user is None:
            pov_user = self.user
        if isinstance(channel, dict):
            kv = channel
        elif channel is not None:
            if channel.id not in self.pinned_yaml:
                await self.update_pins(channel)
            if pov_user.id == self.user.id:
                kv = self.get_yaml_from_channel(channel) | self.pinned_yaml[channel.id]
            else:
                pinned_config = {}
                for message in reversed(self.pins[channel.id]):
                    pinned_config.update(
                        await self.get_config_from_message(
                            message, base_config, pov_user.id
                        )
                    )
                kv = self.get_yaml_from_channel(channel) | pinned_config
        else:
            kv = {}
        config = ontology.load_config_from_kv(kv, base_config.model_dump())
        iface_config = DiscordInterfaceConfig(
            **ontology.transpose_keys(
                ontology.overlay(kv, {"interfaces": [base_iface_config.model_dump()]})
            )["interfaces"][0]
        )
        return config, iface_config

    @contextlib.asynccontextmanager
    async def handle_exceptions(self, message: discord.Message):
        config, iface_config = await self.get_config(None)
        with ot_tracer.start_as_current_span(self.handle_exceptions.__qualname__):
            trace.message.id(message.id, attr=True)
            if isinstance(message.channel, discord.Thread):
                trace.thread.id(message.channel.id, attr=True)
                trace.channel.id(message.channel.parent_id, attr=True)
            else:
                trace.channel.id(message.channel.id, attr=True)
            if hasattr(message, "guild") and message.guild is not None:
                trace.guild.id(message.guild.id, attr=True)
            try:
                async with self.message_semaphore:
                    yield
            except Exception as exc:
                import os, fire, selectors
                from rich.console import Console

                if iface_config.end_to_end_test:
                    self.end_to_end_test_fail = True

                if isinstance(exc, ConfigError):
                    await message.add_reaction("⚙️")
                    print(
                        "bad config in channel",
                        f"#{message.channel.name}",
                        self.get_channel_topic(message.channel),
                    )
                    raise exc.__cause__

                await message.add_reaction("⚠")
                if isinstance(exc, ConnectionError):
                    await message.add_reaction("📵")
                if isinstance(exc, aiohttp.ClientConnectionError):
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

    async def get_my_webhook_for_channel(
        self, channel: discord.TextChannel | discord.Thread
    ) -> discord.Webhook:
        if isinstance(channel, discord.Thread):
            channel = channel.parent

        for webhook in await channel.webhooks():  # perf: uncached
            if webhook.user is not None and webhook.id == self.user.id:
                return webhook
        else:
            return await channel.create_webhook(
                name=self.user.name, avatar=await self.user.avatar.read()
            )

    # async def get_my_webhook_for_channel(
    #     self, channel: discord.TextChannel
    # ) -> discord.Webhook:
    #     for webhook in await channel.webhooks():  # perf: uncached
    #         if webhook.user is not None and webhook.id == self.user.id:
    #             return webhook
    #     else:
    #         return await channel.create_webhook(
    #             name=self.user.name, avatar=await self.user.avatar.read()
    #         )

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
                channel = await self.fetch_channel(
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

    async def update_pins(self, channel: discord.abc.Messageable):
        pins = await channel.pins()
        self.pinned_messages[channel.id] = {m.id for m in pins}
        self.pins[channel.id] = pins
        config = {}
        # pins() is newest first; new pins should be last and override older ones
        for message in reversed(pins):
            config.update(
                await self.get_config_from_message(
                    message, self.base_config, self.user.id
                )
            )
        self.pinned_yaml[channel.id] = config

    async def on_guild_channel_pins_update(self, channel, _last_pin):
        await self.update_pins(channel)

    async def on_private_channel_pins_update(self, channel, _last_pin):
        await self.update_pins(channel)

    async def on_raw_message_edit(self, payload):
        channel = self.get_channel(payload.channel_id)
        # payload.cached_message might be the old version of the message
        # get the new one, if already cached
        if not (
            (
                message := discord.utils.find(
                    lambda m: m.id == payload.message_id, self.cached_messages
                )
            )
            and (timestamp := payload.data.get("edited_timestamp"))
            and message.edited_at == datetime.fromisoformat(timestamp)
        ):
            try:
                message = await channel.fetch_message(payload.message_id)
            except discord.NotFound:
                pass
        if message:
            self.cache(channel).update(message, False)

        if payload.message_id in self.pinned_messages[channel.id]:
            await self.update_pins(channel)

    async def on_raw_message_delete(self, payload):
        channel = self.get_channel(payload.channel_id)
        self.cache(channel).delete(payload.message_id)
        if payload.message_id in self.pinned_messages[channel.id]:
            await self.update_pins(channel)

    async def get_thread_from_message(self, message: discord.Message):
        # gets the thread associated with a message, if it exists
        try:
            thread = await self.get_channel_cached(message.id)
            if isinstance(thread, discord.threads.Thread):
                return thread
            else:
                return None
        except Exception as e:
            return None

    @staticmethod
    async def should_reply(
        user: discord.User,
        message: discord.Message,
        config: Config,
        iface_config: DiscordInterfaceConfig,
        message_history: ActionHistory,
    ) -> bool:
        if message.guild:  # If in a server
            user = message.guild.get_member(user.id)

        return (
            message.author != user
            and (
                not isinstance(message.channel, discord.abc.GuildChannel)
                or message.channel.permissions_for(message.guild.me).send_messages
            )
            and not (
                iface_config.ignore_dotted_messages
                and re.match(DiscordInterface.DOTTED_MESSAGE_RE, message.content)
            )
            and not (
                iface_config.mute is True
                or DiscordInterface.name_in_list(iface_config.mute, config, user)
            )
            and not (
                iface_config.thread_mute
                and message.channel.type == discord.ChannelType.public_thread
            )
            and (
                len(iface_config.discord_user_whitelist) == 0
                or message.author.id in iface_config.discord_user_whitelist
            )
            and (
                len(iface_config.may_speak) == 0
                or DiscordInterface.name_in_list(iface_config.may_speak, config, user)
            )
            and (
                (iface_config.reply_on_ping and user.mentioned_in(message))
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
                            user.name,
                            *iface_config.nicknames,
                        )
                    )
                )
                # TODO
                # or (
                #     iface_config.reply_on_sim
                #     and await deserves_reply(
                #         self.generate_response,
                #         config,
                #         message_history,
                #         iface_config.reply_on_sim,
                #     )
                # )
                or (
                    iface_config.reply_on_regex
                    and re.fullmatch(iface_config.reply_on_regex, message.content)
                )
            )
        )

    @staticmethod
    def embed_from_message(message: discord.Message, timestamp: bool = False):
        embed = discord.Embed(description=message.content)
        embed.set_author(
            name=message.author.display_name,
            icon_url=message.author.display_avatar.url,
            url=message.jump_url,
        )
        if timestamp:
            embed.set_footer(text=message.created_at.strftime("%Y-%m-%d %H:%M:%S"))
        return embed

    @staticmethod
    def realize_pings(
        message_content: str,
        mentions: set[Union[discord.User, discord.Member]],
    ):
        for member in mentions:
            if "@" + member.name in message_content:
                message_content = message_content.replace(
                    "@" + member.name, f"<@!{member.id}>"
                )
        return message_content

    @staticmethod
    def get_yaml_from_channel(
        channel: "discord.abc.MessageableChannel",
    ) -> JSON:
        topic = DiscordInterface.get_channel_topic(channel)
        if topic is not None and "---" in topic:
            try:
                return DiscordInterface.parse_yaml_config(topic) or {}
            except Exception as e:
                print(f"Error parsing YAML in channel {channel.name}: {e}")
                return {}
        else:
            return {}

    @staticmethod
    def parse_yaml_config(content: str):
        content = (
            content.split("---")[1].replace("```yaml", "").replace("```", "").strip()
        )
        return yaml.safe_load(content)

    @staticmethod
    def parse_dot_command(message: discord.Message):
        match = re.match(
            r"^\.(\w+)(?:[\s|\.]+(.+))?$", message.content.split("---", 1)[0]
        )
        if match:
            try:
                yaml_content = DiscordInterface.parse_yaml_config(message.content)
            except Exception as e:
                # print(f"Error parsing YAML {message.content}")
                yaml_content = {}
            return {
                "command": match.group(1),
                "args": re.split("[\s|\.]", match.group(2)) if match.group(2) else [],
                "yaml": yaml_content or {},
            }
        else:
            return None

    @staticmethod
    def parse_history_config(config: dict):
        first_link = config.get("first", None)
        last_link = config.get("last", None)
        root_link = config.get("root", None)
        passthrough = config.get("passthrough", False)
        return first_link, last_link, root_link, passthrough

    @staticmethod
    async def parse_attachment(attachment: discord.Attachment):
        att_info = {"command": None, "args": [], "type": attachment.content_type}
        if (
            attachment.height is not None
            and attachment.width is not None
            and attachment.content_type.startswith("image/")
        ):
            att_info["type"] = "image"
        elif not attachment.content_type or attachment.content_type.startswith("text/"):
            att_info["type"] = "text"
            att_info["content"] = await DiscordInterface.get_attachment_content(
                attachment
            )
            match = re.match(
                r"^\.?(.+?)(?:[\s|-](.+?))?(?:\.(.+?))?$", attachment.filename
            )
            if match:
                try:
                    att_info["yaml"] = yaml.safe_load(att_info["content"])
                    att_info["command"] = match.group(1)
                    att_info["args"] = (
                        re.split("[\s|-]", match.group(2)) if match.group(2) else []
                    )
                except Exception as e:
                    # print(f"Error parsing attachment {attachment.filename}")
                    pass
        return att_info

    @staticmethod
    def get_channel_topic(
        channel: "discord.abc.MessageableChannel",
    ) -> str | None:
        if hasattr(channel, "topic"):
            return channel.topic
        elif hasattr(channel, "parent"):
            return channel.parent.topic
        else:
            return None

    @staticmethod
    async def get_attachment_content(attachment: discord.Attachment) -> str:
        # @lru_cache doesn't work on async functions, so use this as a workaround
        return await asyncio.to_thread(
            DiscordInterface.get_attachment_content_inner, attachment
        )

    @staticmethod
    @lru_cache
    def get_attachment_content_inner(attachment: discord.Attachment):
        r = requests.get(attachment.url, allow_redirects=True)
        attachment_content = r.content
        decoded_content = attachment_content.decode("utf-8")  # Assuming UTF-8 encoding
        unescaped_content = DiscordInterface.unescape_string(decoded_content)
        return unescaped_content

    @staticmethod
    def unescape_string(escaped_string: str) -> str:
        try:
            # Use ast.literal_eval to safely evaluate the string
            return ast.literal_eval(f"'''{escaped_string}'''")
        except (SyntaxError, ValueError):
            # If there's an error, return the original string
            return escaped_string

    @staticmethod
    def message_invisible(
        message: discord.Message, iface_config: DiscordInterfaceConfig
    ):
        if is_continue_command(message.content):
            return True
        elif is_mu_command(message.content):
            return True
        elif iface_config.ignore_system_messages and (message.is_system()):
            return True
        elif iface_config.ignore_dotted_messages and (
            re.match(DiscordInterface.DOTTED_MESSAGE_RE, message.content)
        ):
            return True
        if iface_config.ignore_react_enabled:
            for reaction in message.reactions:
                if reaction.emoji == "🫥":
                    return True
        return False

    @staticmethod
    def config_applies_to_user(
        message: discord.Message,
        pov_config: Optional[Config] = None,
        pov_user_id: Optional[int] = None,
    ):
        dot_command = DiscordInterface.parse_dot_command(message)
        if dot_command:
            if (
                len(dot_command["args"]) == 0
                or DiscordInterface.name_in_list(dot_command["args"], config=pov_config)
                or pov_user_id in message.raw_mentions
            ):
                return True
        return False

    @staticmethod
    async def get_config_from_message(
        message: discord.Message,
        pov_config: Optional[Config] = None,
        pov_user_id: Optional[int] = None,
    ):
        config = {}
        is_config_message = False
        dot_command = DiscordInterface.parse_dot_command(message)
        if dot_command:
            if dot_command["command"] == "config" and (
                DiscordInterface.config_applies_to_user(
                    message, pov_config, pov_user_id
                )
            ):
                config = dot_command["yaml"]
                is_config_message = True
        for attachment in message.attachments:
            att_data = await DiscordInterface.parse_attachment(attachment)
            if att_data["type"] == "text" and (
                DiscordInterface.config_applies_to_user(
                    message, pov_config, pov_user_id
                )
            ):
                if att_data["command"] == "config":
                    config.update(att_data["yaml"])
                    is_config_message = True
                elif is_config_message:
                    config[att_data["command"]] = att_data["yaml"]
        return config

    @staticmethod
    def name_in_list(
        name_list,
        config: Optional[Config] = None,
        user: Optional[discord.User] = None,
        nicknames: list[str] = [],
    ):
        if isinstance(name_list, str):
            name_list = [name_list]
        elif not isinstance(name_list, list):
            return False

        username = user.name if user else None

        return any(
            # name in name_list for name in (self.user.name, self.sysname, *nicknames)
            name in name_list
            for name in (username, config.em.emname, *nicknames)
        )

    @staticmethod
    async def webhook_send(
        webhook: discord.Webhook, channel: discord.abc.Messageable, **kwargs
    ):
        if isinstance(channel, discord.Thread):
            return await webhook.send(**kwargs, thread=channel, wait=True)
        else:
            return await webhook.send(**kwargs, wait=True)


def is_continue_command(message_content: str):
    return message_content.strip() == "/continue" or message_content.startswith(
        "m continue"
    )


def is_mu_command(message_content: str):
    return message_content.strip() == "/mu" or message_content.startswith("m mu")


async def wait_until_timestamp(timestamp, coroutine):
    current_time = time.time()
    if timestamp > current_time:
        # to reduce latency, only send a typing event if there is an actual delay
        async with coroutine():
            await asyncio.sleep(timestamp - current_time)


def isempty(string):
    return string == "" or string.isspace()


class ConfigError(ValueError):
    pass


class ChannelCache:
    def __init__(self, channel: discord.TextChannel):
        self.channel = channel
        self.messages: dict[int, Optional[discord.Message]] = {}
        # message id: is next (by iteration/reverse chronological order) message in cache?
        # like a linked list, but we use SortedDict.irange() for iteration/traversal
        # could be merged with messages, but risk of race conditions with message deletions
        self.sparse: SortedDict[int, bool] = SortedDict()
        self.up_to_date = False

    def set_prev(self, index: int, func: Callable[[bool, bool], bool]) -> bool:
        if len(self.sparse) == 0 or index >= self.sparse.keys()[-1]:
            self.up_to_date = func(old := self.up_to_date, True)
        else:
            # get previous (against iteration/reverse chronological order) index
            prev = next(
                self.sparse.irange(
                    minimum=index, reverse=False, inclusive=(False, False)
                )
            )
            self.sparse[prev] = func(old := self.sparse[prev], False)
        return old

    def update(self, message, latest: bool):
        "latest: if this message is the last message in the channel"

        # in case a message comes in after its deletion. see delete()
        # (possible with proxy bots)
        if self.messages.get(message.id, True) is None:
            return

        if old := self.messages.get(message.id):
            # in case of race condition, only record more recent edits
            if not message.edited_at or message.edited_at <= old.edited_at:
                return

        self.messages[message.id] = message
        # if message.id == self.channel.last_message_id:
        if latest:
            # [ b ...] -> [-a b ...]
            # [-b ...] -> [-a-b ...]
            # or, in case we get messages out of order, whether due to API error or asyncio:
            # [ a c ...] -> [ a b c ...]
            # [-a c ...] -> [-a b c ...]
            # [ a-c ...] -> [ a-b-c ...]
            # [-a-c ...] -> [-a-b-c ...]
            # (no idea if this happens, but let's try to be fault tolerant)
            self.sparse[message.id] = self.set_prev(
                message.id, lambda prev, last: last or prev
            )

    def delete(self, id: int):
        self.messages[id] = None
        if id in self.sparse:
            # a b c -> a c
            # a-b c -> a c
            # a b-c -> a c
            # a-b-c -> a-c
            # or, if this is the first message and the next message is known, we're still up to date
            # (same as diagram above, but "a" is the up_to_date flag)
            self.set_prev(id, lambda prev, last: prev & self.sparse[id])
            del self.sparse[id]

    async def history(
        self,
        limit: Optional[int] = 100,  # same as API default
        before: Optional[discord.Message] = None,
        after: Optional[discord.Message] = None,
    ) -> AsyncIterator[discord.Message]:
        remaining = limit
        beforeid: Optional[int] = before and before.id
        afterid: Optional[int] = after and after.id

        # last cached message; to "link" with any fetched after this
        last: Optional[int] = None
        # if before isn't in cache and marked, we have no way of knowing if the "first" cached message is really the first
        if (before is None and self.up_to_date) or self.sparse.get(beforeid):
            # iter during addition/deletion is an error so make a copy
            for index, value in [
                (k, self.sparse[k])
                for k in self.sparse.irange(
                    minimum=afterid,
                    maximum=beforeid,
                    # we need to watch out for the after message, even if it won't be yielded
                    inclusive=(True, False),
                    reverse=True,
                )
            ]:
                if index == afterid:
                    return

                # might have been deleted after the copy was made
                if message := self.messages.get(index):
                    yield message
                    remaining = remaining and remaining - 1
                    if remaining == 0:
                        return
                    last = index

                if not value:
                    break  # last item in the "linked list"

        # just fetch the rest to keep it simple for now
        # if you've been wondering "wait, why does this need a SortedDict, can't you just use a normal dict with explicit next/prev references"
        # we'll really need the SortedDict to improve this
        first = True
        async for message in self.channel.history(
            # oldest_first defaults to True if after is given
            limit=remaining,
            before=discord.Object(last) if last else before,
            after=after,
            oldest_first=False,
        ):
            # if this message wasn't already cached, assume it's the last one in the "linked list"
            # (at this point we don't know how much more history will be read before the generator is discarded)
            self.sparse[message.id] = self.sparse.get(message.id, False)
            if last:
                # mark the last yielded message, esp. from cache, if any
                # (we want to join the "linked lists" if possible)
                # note that before might be a message that we don't have
                # that's why we don't initialize last = before
                self.sparse[last] = True
            self.update(message, first and (last or before) is None)
            yield message
            first = False
            last = message.id


async def test_cache():
    randrange = random.randrange
    choice = random.choice

    class Message(discord.Object):
        def __repr__(self):
            return str(self.id)

        @property
        def edited_at(self):
            return None

    class Channel:
        def __init__(self):
            self._history = SortedDict(
                {x: Message(x) for x in (randrange(1, 10000) for _ in range(10))}
            )

        def real(
            self,
            limit: Optional[int],
            before: Optional[Message],
            after: Optional[Message],
        ) -> list[Message]:
            return [
                self._history[i]
                for i in list(
                    self._history.irange(
                        minimum=after and after.id,
                        maximum=before and before.id,
                        inclusive=(False, False),
                        reverse=True,
                    )
                )[:limit]
            ]

        async def history(
            self,
            limit: Optional[int],
            before: Optional[Message],
            after: Optional[Message],
            oldest_first: bool = False,
        ) -> AsyncIterator[Message]:
            nonlocal misses
            for i in self.real(limit, before, after):
                misses += 1
                yield i

    total, misses = 0, 0
    for _ in range(100000):
        channel = Channel()
        cache = ChannelCache(channel)
        log = []
        orig = list(channel._history.keys())
        minid = orig[-1]
        for _ in range(7):
            if choice([True, False]):
                # there was a bug with the same message ID being sent multiple times
                # so make sure that message IDs are strictly increasing
                minid += randrange(3, 1000)
                # ... unless we want to test tolerance to out of order messages
                send = [minid] + ([] if randrange(4) else [minid - 2, minid - 1])
                for i in send:
                    channel._history[i] = Message(i)
                    cache.update(channel._history[i], True)
                log.append(("send", send))
            else:
                del channel._history[id := choice(channel._history.keys())]
                cache.delete(id)
                log.append(("delete", id))

            # this can happen with proxy bots
            if choice([True, False]):
                cache.delete(minid := minid + randrange(1, 1000))
                cache.update(Message(minid), True)

            limit = choice([randrange(1, 12), None])
            after, before = sorted(random.sample(channel._history.keys(), 2))
            # after = choice([Message(after - randrange(2)), None])
            # before = choice([Message(before + randrange(2)), None])
            after = choice([Message(after), None])
            before = choice([Message(before), None])
            log.append(("history", limit, before, after))

            cached = [x async for x in cache.history(limit, before, after)]
            real = channel.real(limit, before, after)
            if cached != real:
                breakpoint()
            total += len(real)

    print(f"Hit rate: {total-misses}/{total}={(total-misses)/total}")


class Cache:
    def __init__(self):
        self.channels: dict[int, ChannelCache] = {}

    def __call__(self, channel: discord.TextChannel) -> ChannelCache:
        if channel.id not in self.channels:
            self.channels[channel.id] = ChannelCache(channel)
        return self.channels[channel.id]
