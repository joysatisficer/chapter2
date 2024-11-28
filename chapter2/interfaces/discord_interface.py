import asyncio
import contextlib
import re
import string
import textwrap
import time
import random
from datetime import datetime
from typing import Self, Tuple, Optional, Union, AsyncIterator
from collections.abc import Callable
from collections import defaultdict
from functools import lru_cache
from io import StringIO
import json
import pprint
import enum
import typing

import aiohttp
import discord
import discord.http
import discord.threads
from discord import app_commands
from discord.ext import commands
import pydantic
import yaml
import requests
import ast
from pydantic import ValidationError
from sortedcontainers import SortedDict
from asgiref.sync import sync_to_async
from aioitertools.more_itertools import take as async_take

import ontology
from faculties.contrib.airtable_notes_faculty import get_airtable
from message_formats import hashint
from trace import trace, ot_tracer, log_trace_id_to_console
from interfaces.deserves_reply import deserves_reply
from util.asyncutil import async_generator_to_reusable_async_iterable, run_task
from util.discord_improved import ScheduleTyping, parse_discord_content
from declarations import GenerateResponse, Message, UserID, Author, JSON, ActionHistory
from ontology import Config, DiscordInterfaceConfig
from generate_response import get_prompt
from pathlib import Path
from load import load_em_kv
from util.steering_api import INDEX_TO_DESC, USABLE_FEATURES

from util.app_info import get_emname_id_map, get_steerable_ems


def clean_config_dict(config_dict: dict | list, blacklisted_keys: list[str] = []):
    # recursively remove any keys that are in the blacklisted_keys list
    if isinstance(config_dict, list):
        for item in config_dict:
            clean_config_dict(item, blacklisted_keys)
    elif isinstance(config_dict, dict):
        for key, value in list(config_dict.items()):
            if key in blacklisted_keys:
                del config_dict[key]
            elif isinstance(value, dict) or isinstance(value, list):
                clean_config_dict(value, blacklisted_keys)
    return config_dict


async def load_em_configs(emname):
    config_kv = load_em_kv(emname)
    defaults = ontology.get_defaults(Config)
    config = ontology.load_config_from_kv(config_kv, defaults)
    iface_config = config.interfaces[0]
    return config, iface_config


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


DOTTED_MESSAGE_RE = r"^[.,][^\s.,]"


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

        if self.iface_config.infra:
            self.tree = app_commands.CommandTree(self)
            self.emname_to_id = get_emname_id_map()
            self.id_to_emname = {v: k for k, v in self.emname_to_id.items()}
            self.steerable_ems = get_steerable_ems()

    async def resolve_message(
        self,
        interaction: discord.Interaction,
        message_link: Optional[str] = None,
        require_regular_msg: bool = True,
    ):
        if message_link is None:
            if require_regular_msg:
                message = await last_normal_message(interaction.channel)
            else:
                message = await last_message(interaction.channel)
        else:
            message = await self.get_message_from_link(message_link)
        return message

    async def load_pov(
        self,
        emname: Optional[str] = None,
        message: Optional[discord.Message] = None,
    ):
        config, iface_config = (
            await load_em_configs(emname)
            if emname is not None
            else (self.base_config, self.iface_config)
        )
        if emname is not None:
            discord_id = self.emname_to_id[emname]
            if discord_id is None:
                raise ValueError(f"No discord ID for emname {emname}")
            pov_user = await self.fetch_user(discord_id)
        else:
            pov_user = self.user

        if message is not None:
            try:
                config, iface_config = await self.get_config(
                    message.channel, config, iface_config, pov_user
                )
            except (ValueError, ValidationError) as exc:
                raise ConfigError() from exc
        return config, iface_config, pov_user

    async def interaction_wrapper(
        self,
        command_name: str,
        func,
        **kwargs,
    ):
        interaction = kwargs["interaction"]
        ephemeral = not kwargs.get("public", True)
        await interaction.response.defer(ephemeral=ephemeral)
        try:
            if "message_link" in kwargs and kwargs.get("message", None) is None:
                kwargs["message"] = await self.resolve_message(
                    interaction,
                    kwargs["message_link"],
                    kwargs.get("require_regular_msg", False),
                )
            if "pov" in kwargs:
                kwargs["config"], kwargs["iface_config"], kwargs["pov_user"] = (
                    await self.load_pov(kwargs["pov"], kwargs["message"])
                )
            await func(**kwargs)
            if not interaction.response.is_done():
                await interaction.followup.send(
                    f"✓ **{command_name}** executed successfully",
                )
        except Exception as e:
            print(f'Error handling "{command_name}" command: {e}')
            await interaction.followup.send(
                f"Error handling **{command_name}** command: {e}",
            )

    async def setup_hook(self):
        if self.iface_config.infra:
            em_users = []
            for discord_id, emname in self.id_to_emname.items():
                user = await self.fetch_user(discord_id)
                em_users.append(
                    {
                        "user": user,
                        "emname": emname,
                        "steerable": emname in self.steerable_ems,
                    }
                )

            async def em_users_autocomplete(
                interaction: discord.Interaction, current: str
            ):
                # filter em_users by users that are in the server
                guild = interaction.guild
                matching_em_members = [
                    user for user in em_users if user["user"] in guild.members
                ]
                matches = [
                    user
                    for user in matching_em_members
                    if current.lower() in user["emname"].lower()
                    or current.lower() in user["user"].display_name.lower()
                ]
                return [
                    app_commands.Choice(
                        name=user["user"].display_name, value=user["emname"]
                    )
                    for user in matches[:25]
                ]

            async def steerable_users_autocomplete(
                interaction: discord.Interaction, current: str
            ):
                guild = interaction.guild
                steerable_users = [
                    user
                    for user in em_users
                    if user["steerable"] and user["user"] in guild.members
                ]
                matches = [
                    user
                    for user in steerable_users
                    if current.lower() in user["emname"].lower()
                    or current.lower() in user["user"].display_name.lower()
                ]
                return [
                    app_commands.Choice(
                        name=user["emname"] + f" ({user['user'].display_name})",
                        value=user["emname"],
                    )
                    for user in matches[:25]
                ]

            async def feature_autocomplete(
                interaction: discord.Interaction, current: str
            ):
                matching_features = [
                    feature
                    for feature in USABLE_FEATURES
                    if current.lower() in feature["desc"].lower()
                ]
                return [
                    app_commands.Choice(
                        name=str(feature["index"]) + f" ({feature['desc']})",
                        value=str(feature["index"]),
                    )
                    for feature in matching_features[:25]
                ]

            async def current_features_autocomplete(
                interaction: discord.Interaction, current: str
            ):
                emname = self.steerable_ems[0]
                message = await last_normal_message(interaction.channel)
                config, _, _ = await self.load_pov(emname, message)
                try:
                    config_dict = config.em.model_dump()
                    current_configuration = config_dict["continuation_options"][
                        "steering"
                    ]["feature_levels"]
                except:
                    current_configuration = {}
                current_features = [
                    feature.split("_")[-1] for feature in current_configuration.keys()
                ]
                return [
                    app_commands.Choice(
                        name=feature + f" ({INDEX_TO_DESC[int(feature)]})",
                        value=feature,
                    )
                    for feature in current_features[:25]
                ]

            async def targets_autocomplete(
                interaction: discord.Interaction, current: str
            ):
                targets = current.split(" ")
                prefix = " ".join(targets[:-1])
                guild = interaction.guild
                matches = [user for user in em_users if user["user"] in guild.members]
                matches = [user for user in matches if user["emname"] not in prefix]
                matches = [
                    user
                    for user in matches
                    if targets[-1].lower() in user["emname"].lower()
                ]
                return [
                    app_commands.Choice(
                        name=prefix + f" {user['emname']}",
                        value=prefix + f" {user['emname']}",
                    )
                    for user in matches[:25]
                ]

            async def config_keys_autocomplete(
                interaction: discord.Interaction, current: str
            ):
                interface_keys = ontology.ALL_INTERFACE_KEYS.copy()
                interface_keys.update(ontology.SHARED_INTERFACE_KEYS)
                em_keys = ontology.EM_KEYS.copy()
                all_keys = interface_keys.union(em_keys)
                blacklisted_keys = {
                    "folder",
                    "novelai_api_key",
                    "exa_search_api_key",
                    "vendors",
                    "discord_token",
                    "discord_proxy_url",
                }
                all_keys = all_keys - blacklisted_keys
                matches = [key for key in all_keys if current.lower() in key.lower()]
                return [
                    app_commands.Choice(name=key, value=key) for key in matches[:25]
                ]

            format_names = ["irc", "colon", "infrastruct", "chat"]
            message_history_formats = [
                app_commands.Choice(name=format_name, value=format_name)
                for format_name in format_names
            ]

            try:

                @self.tree.command(name="fork", description="forks a thread")
                @app_commands.describe(
                    message_link="message to fork into a new thread",
                    public="(TRUE by default) create a public thread. If FALSE, create a private thread.",
                    title="optional title for the forked thread",
                )
                async def fork(
                    interaction: discord.Interaction,
                    message_link: Optional[str] = None,
                    public: bool = True,
                    title: Optional[str] = None,
                ):
                    await self.interaction_wrapper(
                        command_name="/fork",
                        func=self.fork_command,
                        interaction=interaction,
                        message_link=message_link,
                        public=public,
                        title=title,
                    )

                @self.tree.command(
                    name="mu",
                    description="fork thread from message parent and regenerate message",
                )
                @app_commands.describe(
                    message_link="message to regenerate",
                    public="(TRUE by default) create a public thread. If FALSE, create a private thread.",
                    title="optional title for the forked thread",
                )
                async def mu(
                    interaction: discord.Interaction,
                    message_link: Optional[str] = None,
                    public: bool = True,
                    title: Optional[str] = None,
                ):
                    await self.interaction_wrapper(
                        command_name="/mu",
                        func=self.mu_command,
                        interaction=interaction,
                        message_link=message_link,
                        public=public,
                        title=title,
                        require_regular_msg=True,
                    )

                @self.tree.command(
                    name="prompt", description="send the prompt of a message"
                )
                @app_commands.autocomplete(pov=em_users_autocomplete)
                @app_commands.describe(
                    message_link="message to send the prompt of (prompt excludes message)",
                    pov="em from whose POV to build prompt. defaults to author of message if possible.",
                    public="(FALSE by default) interaction is visible to the rest of the server",
                )
                async def prompt(
                    interaction: discord.Interaction,
                    message_link: Optional[str] = None,
                    pov: Optional[str] = None,
                    public: bool = False,
                ):
                    # TODO use transcript function if POV not specified
                    message = None
                    if message_link is not None and pov is None:
                        message = await self.get_message_from_link(message_link)
                        if str(message.author.id) in self.id_to_emname:
                            pov = self.id_to_emname[str(message.author.id)]
                    await self.interaction_wrapper(
                        command_name="/prompt",
                        func=self.get_context_command,
                        interaction=interaction,
                        message_link=message_link,
                        message=message,
                        pov=pov,
                        inclusive=(message_link is None),
                        public=public,
                    )

                @self.tree.command(
                    name="history",
                    description="splice history range into context by sending a .history message",
                )
                @app_commands.autocomplete(targets=targets_autocomplete)
                @app_commands.describe(
                    targets="space-separated list of ems to apply history splice to. by default, all ems are affected.",
                    last="link to last message to include in history splice",
                    first="link to first message to include in history splice",
                    passthrough="(FALSE by default) if true, messages before the .history splice are still included in the history",
                )
                async def history(
                    interaction: discord.Interaction,
                    targets: Optional[str] = None,
                    last: Optional[str] = None,
                    first: Optional[str] = None,
                    passthrough: bool = None,
                ):
                    config_dict = {
                        "first": first,
                        "last": last,
                        "passthrough": passthrough,
                    }
                    if targets is not None:
                        targets = targets.split(" ")
                    await self.interaction_wrapper(
                        command_name="/history",
                        func=self.send_config_command,
                        interaction=interaction,
                        command_prefix="history",
                        config_dict=config_dict,
                        targets=targets,
                    )

                @self.tree.command(
                    name="transcript", description="get transcript between two messages"
                )
                @app_commands.choices(transcript_format=message_history_formats)
                @app_commands.describe(
                    first_link="first message in transcript. defaults to first message in channel",
                    last_link="last message in transcript. defaults to last message in channel",
                    transcript_format="transcript format",
                    public="(FALSE by default) interaction is visible to the rest of the server",
                )
                async def transcript(
                    interaction: discord.Interaction,
                    first_link: Optional[str] = None,
                    last_link: Optional[str] = None,
                    transcript_format: Optional[str] = "colon",
                    public: bool = False,
                ):
                    await self.interaction_wrapper(
                        command_name="/transcript",
                        func=self.transcript_command,
                        interaction=interaction,
                        first_link=first_link,
                        last_link=last_link,
                        transcript_format=transcript_format,
                        public=public,
                    )

                @self.tree.command(
                    name="config",
                    description="update configuration for channel (pins .config message)",
                )
                @app_commands.autocomplete(targets=targets_autocomplete)
                @app_commands.choices(
                    message_history_format=message_history_formats,
                )
                async def configure(
                    interaction: discord.Interaction,
                    targets: Optional[str] = None,
                    name: Optional[str] = None,
                    continuation_model: Optional[str] = None,
                    recency_window: Optional[int] = None,
                    continuation_max_tokens: Optional[int] = None,
                    temperature: Optional[float] = None,
                    top_p: Optional[float] = None,
                    frequency_penalty: Optional[float] = None,
                    presence_penalty: Optional[float] = None,
                    split_message: Optional[bool] = None,
                    message_history_format: Optional[str] = None,
                    reply_on_random: Optional[int] = None,
                    ignore_dotted_messages: Optional[bool] = None,
                    # yaml: Optional[discord.Attachment] = None,
                ):
                    if targets is not None:
                        targets = targets.split(" ")
                    config_dict = locals().copy()
                    del config_dict["interaction"]
                    del config_dict["targets"]
                    del config_dict["self"]
                    if message_history_format is not None:
                        config_dict["message_history_format"] = {
                            "name": message_history_format
                        }
                    await self.interaction_wrapper(
                        command_name="/config",
                        func=self.send_config_command,
                        interaction=interaction,
                        command_prefix="config",
                        config_dict=config_dict,
                        targets=targets,
                    )

                @self.tree.command(
                    name="reset_config",
                    description="reset configuration for channel (unpins all .config messages)",
                )
                async def reset_config(
                    interaction: discord.Interaction,
                ):
                    await self.interaction_wrapper(
                        command_name="/reset_config",
                        func=self.reset_config_command,
                        interaction=interaction,
                    )

                @self.tree.command(
                    name="get_config",
                    description="get the local config state of an em",
                )
                @app_commands.autocomplete(pov=em_users_autocomplete)
                @app_commands.autocomplete(property=config_keys_autocomplete)
                @app_commands.describe(
                    pov="em to get the local config state of",
                    property="property to get. if none, get all properties",
                    # message_link="location to get the local config state. defaults to last message in channel",
                    public="(FALSE by default) interaction is visible to the rest of the server",
                )
                async def get_config(
                    interaction: discord.Interaction,
                    pov: str,
                    property: Optional[str] = None,
                    # message_link: Optional[str] = None,
                    public: bool = False,
                ):
                    await self.interaction_wrapper(
                        command_name="/get_config",
                        func=self.get_cleaned_config,
                        interaction=interaction,
                        pov=pov,
                        property=property,
                        message_link=None,
                        public=public,
                    )

                if len(self.steerable_ems) > 0:

                    @self.tree.command(
                        name="set_vector",
                        description="configure claude 3 sonnet steering vector",
                    )
                    @app_commands.autocomplete(target=steerable_users_autocomplete)
                    @app_commands.autocomplete(feature=feature_autocomplete)
                    @app_commands.describe(
                        target="em to configure the steering vector of",
                        feature="feature to configure",
                        level="feature level(-10 to 10)",
                        reset="(FALSE by default) reset previously configured vectors",
                    )
                    async def set_vector(
                        interaction: discord.Interaction,
                        target: str,
                        feature: str,
                        level: float,
                        # level: Optional[float] = None,
                        reset: bool = False,
                    ):
                        await self.interaction_wrapper(
                            command_name="/set_vector",
                            func=self.config_vector_command,
                            interaction=interaction,
                            command_prefix="config",
                            pov=target,
                            feature=feature,
                            level=level,
                            reset=reset,
                            targets=[target],
                            message_link=None,
                        )

                    @self.tree.command(
                        name="remove_vector",
                        description="remove a steering vector",
                    )
                    @app_commands.autocomplete(feature=current_features_autocomplete)
                    @app_commands.autocomplete(target=steerable_users_autocomplete)
                    @app_commands.describe(
                        target="em to remove the steering vector of",
                        feature="feature to remove",
                    )
                    async def remove_vector(
                        interaction: discord.Interaction,
                        target: str,
                        feature: str,
                    ):
                        await self.interaction_wrapper(
                            command_name="/remove_vector",
                            func=self.config_vector_command,
                            interaction=interaction,
                            command_prefix="config",
                            pov=target,
                            feature=feature,
                            level=None,
                            reset=False,
                            message_link=None,
                            targets=[target],
                        )

                    @self.tree.command(
                        name="reset_vectors",
                        description="reset all steering vectors",
                    )
                    @app_commands.autocomplete(target=steerable_users_autocomplete)
                    @app_commands.describe(
                        target="em to reset the steering vectors of",
                    )
                    async def reset_vectors(
                        interaction: discord.Interaction,
                        target: str,
                    ):
                        config_dict = {
                            "continuation_options": {"steering": {"feature_levels": {}}}
                        }
                        await self.interaction_wrapper(
                            command_name="/reset_vectors",
                            func=self.send_config_command,
                            interaction=interaction,
                            command_prefix="config",
                            config_dict=config_dict,
                            targets=[target],
                        )

                    @self.tree.command(
                        name="get_vectors",
                        description="show current steering state",
                    )
                    @app_commands.autocomplete(pov=steerable_users_autocomplete)
                    @app_commands.describe(
                        pov="em to show the steering vectors configuration of",
                        # message_link="location to show the steering configuration of. defaults to last message in channel",
                        public="(FALSE by default) interaction is visible to the rest of the server",
                    )
                    async def steering_state(
                        interaction: discord.Interaction,
                        pov: str,
                        # message_link: Optional[str] = None,
                        public: bool = False,
                    ):
                        await self.interaction_wrapper(
                            command_name="/get_vectors",
                            func=self.steering_state_command,
                            interaction=interaction,
                            pov=pov,
                            message_link=None,
                            public=public,
                        )

            except Exception as e:
                print(f"Error registering slash command: {e}")
                exit(1)

            try:

                # async def private_fork_menu_command(
                #     interaction: discord.Interaction, message: discord.Message
                # ):
                #     await self.interaction_wrapper(
                #         command_name="/fork",
                #         func=self.fork_command,
                #         interaction=interaction,
                #         message=message,
                #         public=False,
                #         title=None,
                #     )

                async def public_fork_menu_command(
                    interaction: discord.Interaction, message: discord.Message
                ):
                    await self.interaction_wrapper(
                        command_name="/fork",
                        func=self.fork_command,
                        interaction=interaction,
                        message=message,
                        public=True,
                        title=None,
                    )

                async def mu_menu_command(
                    interaction: discord.Interaction, message: discord.Message
                ):
                    await self.interaction_wrapper(
                        command_name="/mu",
                        func=self.mu_command,
                        interaction=interaction,
                        message=message,
                        public=True,
                        title=None,
                    )

                # async def get_history_context_command(
                #     interaction: discord.Interaction, message: discord.Message
                # ):
                #     await self.interaction_wrapper(
                #         command_name="/prompt",
                #         func=self.get_context_command,
                #         interaction=interaction,
                #         message=message,
                #         pov=None,
                #     )

                # create_private_fork_command = app_commands.ContextMenu(
                #     name="fork (private)",
                #     callback=private_fork_menu_command,
                #     type=discord.AppCommandType.message,
                # )

                create_public_fork_command = app_commands.ContextMenu(
                    name="fork",
                    callback=public_fork_menu_command,
                    type=discord.AppCommandType.message,
                )

                mu_command = app_commands.ContextMenu(
                    name="mu",
                    callback=mu_menu_command,
                    type=discord.AppCommandType.message,
                )

                # get_history_command = app_commands.ContextMenu(
                #     name="get context",
                #     callback=get_history_context_command,
                #     type=discord.AppCommandType.message,
                # )

                # self.tree.add_command(get_history_command)
                # self.tree.add_command(create_private_fork_command)
                self.tree.add_command(create_public_fork_command)
                self.tree.add_command(mu_command)

                # reset the tree

            except Exception as e:
                print(f"Error registering context menu command: {e}")
                exit(1)

            sync_result = await self.tree.sync()

            # print(
            #     f"Sync completed. Registered {len(sync_result)} commands: {[cmd.name for cmd in sync_result]}"
            # )

    async def send_config_command(self, **kwargs):
        interaction = kwargs["interaction"]
        command_prefix = kwargs.get("command_prefix", ".config")
        config_dict = kwargs.get("config_dict", None)
        targets = kwargs.get("targets", None)
        # targets_array = targets.split(" ") if targets else None
        config_message = compile_config_message(command_prefix, config_dict, targets)
        sent_message = await interaction.followup.send(config_message)
        if sent_message is not None:
            await sent_message.pin()

    async def reset_config_command(self, **kwargs):
        interaction = kwargs["interaction"]
        pins = await interaction.channel.pins()
        unpinned_messages = []
        for pin in pins:
            if pin.content.startswith(".config"):
                await pin.unpin()
                unpinned_messages.append(pin)
        if len(unpinned_messages) > 0:
            content = f"### ✓ unpinned {len(unpinned_messages)} config messages:"
            for message in unpinned_messages:
                content += f"\n- {message.jump_url}"
        else:
            content = "✗ no config messages to unpin"
        await interaction.followup.send(content)

    async def config_vector_command(self, **kwargs):
        config = kwargs["config"]
        pov = kwargs["pov"]
        feature = kwargs["feature"]
        level = kwargs["level"]
        reset = kwargs["reset"]

        feature_key = f"feat_34M_20240604_{feature}"

        try:
            config_dict = config.em.model_dump()
            current_configuration = config_dict["continuation_options"]["steering"][
                "feature_levels"
            ]
        except:
            current_configuration = {}
        config_dict = {
            "continuation_options": {
                "steering": {
                    "feature_levels": current_configuration if not reset else {}
                }
            }
        }
        # feature = feature.value
        if level is not None:
            config_dict["continuation_options"]["steering"]["feature_levels"][
                feature_key
            ] = level
        else:
            del config_dict["continuation_options"]["steering"]["feature_levels"][
                feature_key
            ]

        kwargs["config_dict"] = config_dict
        await self.send_config_command(**kwargs)

    async def steering_state_command(self, **kwargs):
        interaction = kwargs["interaction"]
        pov = kwargs["pov"]
        pov_user = kwargs["pov_user"]
        config = kwargs["config"]
        try:
            config_dict = config.em.model_dump()
            current_configuration = config_dict["continuation_options"]["steering"][
                "feature_levels"
            ]
        except:
            current_configuration = {}

        if len(current_configuration) == 0:
            content = f"✗ no steering vectors configured for {pov_user.mention}"
        else:
            content = f"### :information_source: current steering vectors for {pov_user.mention}:\n"
            # content += (
            #     "```yaml\n"
            #     + yaml.dump({"feature_levels": current_configuration})
            #     + "\n```"
            # )
            for feature, level in current_configuration.items():
                index = int(feature.split("_")[-1])
                content += f'- `{index}` ("{INDEX_TO_DESC[index]}"): `{level}`\n'

        await interaction.followup.send(content)

    async def get_cleaned_config(self, **kwargs):
        interaction = kwargs["interaction"]
        message = kwargs["message"]
        config = kwargs["config"]
        pov = kwargs["pov"]
        pov_user = kwargs["pov_user"]

        property = kwargs.get("property", None)
        cleaned_config = clean_config_dict(
            config.model_dump(),
            [
                "folder",
                "novelai_api_key",
                "exa_search_api_key",
                "vendors",
                "discord_token",
                "discord_proxy_url",
            ],
        )
        if property is not None:
            flattened_config = cleaned_config["em"] | cleaned_config["interfaces"][0]
            property_config = flattened_config.get(property, None)
            if property_config is None:
                content = f"✗ property `{property}` not found"
            else:
                content = f":information_source: local `{property}` config for {pov_user.mention}:"
                content += (
                    "```yaml\n" + yaml.dump({property: property_config}) + "\n```"
                )
            await interaction.followup.send(content)
        else:
            file = discord.File(
                StringIO(yaml.dump(cleaned_config)),
                filename=f"{pov}-config.yaml",
            )
            await interaction.followup.send(
                f"### :information_source: local config for {pov_user.mention}:",
                file=file,
            )

    async def get_context_command(
        self,
        **kwargs,
    ):
        interaction = kwargs["interaction"]
        message = kwargs["message"]
        config = kwargs["config"]
        iface_config = kwargs["iface_config"]
        pov = kwargs["pov"]
        pov_user = kwargs["pov_user"]
        inclusive = kwargs.get("inclusive", True)
        message_history = lambda message, first_message=None, config=config, iface_config=iface_config, pov_user=pov_user, inclusive=inclusive: self.message_history(
            message, first_message, config, iface_config, pov_user, inclusive
        )

        history, _ = zip(
            *(
                await async_take(
                    config.em.recency_window,
                    async_generator_to_reusable_async_iterable(
                        message_history, message
                    ),
                )
            )
        )

        prompt = await get_prompt(history, config.em)

        file = discord.File(
            StringIO(prompt),
            filename="prompt.txt",
        )
        message_content = f"### :page_with_curl: prompt for message {message.jump_url}"
        if pov_user is not None:
            message_content += f" from the perspective of {pov_user.mention}"
        message_content += ":"
        await interaction.followup.send(
            message_content,
            file=file,
        )

    async def transcript_command(self, **kwargs):
        interaction = kwargs["interaction"]
        first_link = kwargs["first_link"]
        last_link = kwargs["last_link"]
        transcript_format = kwargs["transcript_format"]
        if first_link is None:
            first_message = None
        else:
            first_message = await self.get_message_from_link(first_link)
        if last_link is None:
            last_message = await last_normal_message(interaction.channel)
        else:
            last_message = await self.get_message_from_link(last_link)

        config = self.base_config

        if transcript_format is not None:
            config_update = {"message_history_format": {"name": transcript_format}}
            config = ontology.load_config_from_kv(config_update, config.model_dump())

        iface_config = self.iface_config
        pov_user = self.user

        message_history = lambda message, first_message=first_message, config=config, iface_config=iface_config, pov_user=pov_user, inclusive=True: self.message_history(
            message, first_message, config, iface_config, pov_user, inclusive
        )

        message_history_format = config.em.message_history_format

        all_items = [item async for item in message_history(last_message)]
        history = [msg for msg, _ in all_items]

        transcript = "".join(
            message_history_format.render(message) for message in reversed(history)
        )

        file = discord.File(
            StringIO(transcript),
            filename="transcript.txt",
        )

        first_message_url = (
            first_message.jump_url if first_message else "start of channel"
        )

        message_content = f"### :page_with_curl: trancript between {first_message_url} and {last_message.jump_url}:"
        await interaction.followup.send(
            message_content,
            file=file,
        )

    async def fork_command(self, **kwargs):
        interaction = kwargs["interaction"]
        message = kwargs["message"]
        public = kwargs["public"]
        title = kwargs["title"]

        new_thread, history_message = await self.fork_to_thread(
            message=message,
            reason=f"Created by {interaction.user} through {interaction.command.name}",
            public=public,
            title=title,
            interaction=interaction,
        )

        emoji = "✓" if public else "✓ :lock:"

        await interaction.followup.send(
            f".{emoji} **created fork:** {message.jump_url} ⌥ {history_message.jump_url}"
        )

        return new_thread

    async def mu_command(self, **kwargs):
        message = kwargs["message"]
        parent_message = await last_normal_message(message.channel, before=message)
        if parent_message is None:
            raise ValueError("No parent message found")
        kwargs["message"] = parent_message
        new_thread = await self.fork_command(**kwargs)
        message_author = message.author

        await new_thread.send(f"m continue {message_author.mention}")
        # return success_message

    @trace
    async def message_history(
        self,
        message: discord.Message,
        first_message: Optional[discord.Message] = None,
        config: Optional[Config] = None,
        iface_config: Optional[DiscordInterfaceConfig] = None,
        pov_user: Optional[discord.User] = None,
        inclusive: bool = True,
    ):
        if pov_user is None:
            pov_user = self.user
        if inclusive and message and not message_invisible(message, iface_config):
            yield await self.discord_message_to_message(
                config, iface_config, message, pov_user
            )

        async for this_message in self.cache(message.channel).history(
            limit=None,
            before=message,
            after=first_message,
        ):
            if not message or message_invisible(this_message, iface_config):
                pass
            else:
                yield await self.discord_message_to_message(
                    config, iface_config, this_message, pov_user
                )
            config_message = parse_dot_command(this_message)
            if config_message and config_message["command"] == "history":
                if (
                    len(config_message["args"]) == 0
                    or name_in_list(
                        name_list=config_message["args"],
                        config=config,
                        user=pov_user,
                    )
                    or pov_user.mentioned_in(this_message)
                ):
                    if "last" in config_message["yaml"]:
                        last = await self.get_message_from_link(
                            config_message["yaml"]["last"]
                        )
                        first = None
                        if "first" in config_message["yaml"]:
                            first = await self.get_message_from_link(
                                config_message["yaml"]["first"]
                            )
                        if last is not None:
                            if first is None:
                                first = first_message
                            async for msg in self.message_history(
                                last, first, config, iface_config, pov_user
                            ):
                                yield msg
                    if (
                        "passthrough" not in config_message["yaml"]
                        or config_message["yaml"]["passthrough"] is False
                    ):
                        return
        if first_message is not None:
            yield await self.discord_message_to_message(
                config, iface_config, first_message, pov_user
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
                starter_message_id = message.channel.name.split("past:")[1]
            elif thread.id is not None:
                starter_message_id = thread.id
            else:
                return
            try: 
                starter_message = await message.channel.parent.fetch_message(
                    starter_message_id
                )
            except discord.errors.NotFound:
                starter_message = None
            if starter_message is not None:
                async for msg in self.message_history(
                    starter_message,
                    first_message=None,
                    config=config,
                    iface_config=iface_config,
                    pov_user=pov_user,
                ):
                    yield msg

    async def on_message(self, message: discord.Message) -> None:

        self.cache(message.channel).update(message, True)

        if self.iface_config.infra:
            # if message author is not the bot
            if message.author == self.user:
                return
            elif message_invisible(message, self.iface_config):
                return
            # if the message is in a thread and the name of the thread ends with "⌥"
            elif isinstance(
                message.channel, discord.threads.Thread
            ) and message.channel.name.endswith("⌥"):
                # if the parent of the message is a regular message
                parent = await last_normal_message(message.channel, before=message)
                if (
                    parent
                    and not message_invisible(parent, self.iface_config)
                    and parent.author == message.author
                ):
                    return
                name = "⌥ " + message.content[:40] + "..."
                await message.channel.edit(name=name)
            return
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
        # elif message_invisible(message, self.iface_config):
        #     return

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
                    my_user_id = UserID(str(self.user.id), "discord")

                    message_history = lambda message, first_message=None, config=config, iface_config=iface_config: self.message_history(
                        message, first_message, config, iface_config
                    )

                    if not await self.should_reply(
                        message,
                        config,
                        iface_config,
                        my_user_id,
                        async_generator_to_reusable_async_iterable(
                            lambda: (
                                message async for message, _ in message_history(message)
                            )
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
                        my_user_id,
                        history,
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
                                await message.channel.send(
                                    realize_pings(message.channel, content, mentions),
                                )
                                if self.iface_config.exo_enabled:
                                    await self.respond_to_tools(
                                        message.channel, reply_message
                                    )
                                trace.send_message(reply_message.content)
                                first_message = False
                finally:
                    if is_command:
                        await message.delete()

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
        pov_user: Optional[discord.User] = None,
    ) -> Tuple[Message, frozenset[Union[discord.User, discord.Member]]]:
        if pov_user is None:
            pov_user = self.user

        if message.author.id == pov_user.id:
            author_name = config.em.name
        else:
            author_name = message.author.name
        content = parse_discord_content(message, pov_user.id, config.em.name)
        for attachment in message.attachments:
            att_data = await parse_attachment(attachment)
            if iface_config.ignore_dotted_messages and (
                att_data["command"] in ["config", "history"]
                or re.match(DOTTED_MESSAGE_RE, attachment.filename)
            ):
                continue
            if att_data["type"] == "text":
                # don't strip leading whitespace; might be ASCII art
                content += f"\n<|begin_of_attachment|>{(await get_attachment_content(attachment)).rstrip()}<|end_of_attachment|>"
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
                    content = f"<|begin_of_fwd|>{parse_discord_content(forwarded_message, pov_user.id, config.em.name)}<|end_of_fwd|>"
        return Message(
            Author(author_name, UserID(str(message.author.id), "discord")),
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
                iface_config.ignore_dotted_messages
                and re.match(DOTTED_MESSAGE_RE, message.content)
            )
            and not (
                iface_config.mute is True
                or name_in_list(iface_config.mute, config, self.user)
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
                or name_in_list(iface_config.may_speak, config, self.user)
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

    @trace
    async def get_config(
        self,
        channel: "discord.abc.MessageableChannel",
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
                kv = get_yaml_from_channel(channel) | self.pinned_yaml[channel.id]
            else:
                pinned_config = {}
                for message in reversed(self.pins[channel.id]):
                    pinned_config.update(
                        await get_config_from_message(message, base_config, pov_user)
                    )
                kv = get_yaml_from_channel(channel) | pinned_config
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
                        get_channel_topic(message.channel),
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
                # allows bot to use slash commands
                use_application_commands=self.iface_config.infra,
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
                await get_config_from_message(message, self.base_config, self.user)
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

    async def fork_to_thread(
        self,
        interaction: discord.Interaction,
        message: discord.Message,
        title: Optional[str] = None,
        reason: str = "Created by bot",
        public: bool = False,
        # interaction: Optional[discord.Interaction] = None,
        # user: discord.User = None,
    ):
        index_msg = None
        message_thread = None
        if title is None:
            title = "..." + message.content[-15:] + "⌥"
        if not isinstance(message.channel, discord.threads.Thread):
            channel = message.channel
            message_thread = await self.get_thread_from_message(message)
            index_message_prefix = ".:twisted_rightwards_arrows: **futures**"
            if message_thread is not None:
                # if thread attached to message already exists
                # get first message after the thread starter message
                first_message = await get_first_non_system_thread_message(
                    message_thread
                )

                if (
                    first_message is not None
                    and first_message.author == self.user
                    and first_message.content.startswith(index_message_prefix)
                ):
                    index_msg = first_message
            else:
                # message has no thread yet; create a new one for storing links to children messages
                index_thread_name = "..." + message.content[-15:] + "⌥*"
                message_thread = await message.create_thread(
                    name=index_thread_name,
                    reason=reason,
                )
                index_msg = await message_thread.send(
                    content=index_message_prefix + f"\n- {message.jump_url}"
                )
                # get the next message after message in the main channel if it exists

        else:
            channel = message.channel.parent

        embed = embed_from_message(message)
        if index_msg is not None:
            embed.description = (
                message.content
                + f"\n\n-# [:twisted_rightwards_arrows: alt futures]({index_msg.jump_url})"
            )

        if public:
            # if interaction is provided and in the same channel as the message
            # send the message in response to the interaction
            new_thread_message = await channel.send(
                content=f".:rewind:{message.jump_url}",
                embed=embed,
            )
            # new_thread_message = await interaction.followup.send(
            #     content=f".:new: ## branch at {message.jump_url}⌥",
            #     embed=embed,
            # )
            new_thread = await new_thread_message.create_thread(
                name=title, reason=reason
            )
        else:
            new_thread = await channel.create_thread(name=title, reason=reason)
        # embed a copy of the message in the thread with a link to the original message
        history_message = await new_thread.send(
            content=f".history\n---\nlast: {message.jump_url}",
            embed=embed if not public else None,
        )

        # edit children message to point to the history message
        if index_msg is not None:
            # num = children_message.content.count("⌥") + 1
            await index_msg.edit(
                content=index_msg.content + f"\n- {history_message.jump_url}",
            )

        if not public:
            # send a message to the new thread pinging the user to add them
            await new_thread.send(f".{interaction.user.mention}")

        return new_thread, history_message


async def get_first_non_system_thread_message(thread):
    try:
        # Get messages and filter out system messages
        messages = [
            msg
            async for msg in thread.history(after=thread.starter_message, limit=5)
            if not msg.is_system()
            and not msg.type == discord.MessageType.thread_starter_message
        ]

        # Return the first non-system message if there is one
        if messages:
            return messages[0]
        return None

    except Exception as e:
        print(f"Error getting thread message: {e}")
        return None


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


def is_continue_command(message_content: str):
    return message_content.strip() == "/continue" or message_content.startswith(
        "m continue"
    )


def is_mu_command(message_content: str):
    return message_content.strip() == "/mu" or message_content.startswith("m mu")


async def search_for_message_in_channels(
    message_id: int, channels: list[discord.TextChannel | None]
):
    for channel in channels:
        if channel is None:
            continue
        try:
            return await channel.fetch_message(message_id)
        except discord.NotFound:
            pass
    return None


async def last_message(
    channel: discord.abc.Messageable, before: discord.Message = None
):
    # Get the most recent message before the command
    message = [msg async for msg in channel.history(limit=1, before=before)][0]
    return message


async def last_normal_message(
    channel: discord.abc.Messageable, before: discord.Message = None
):
    async for message in channel.history(limit=10, before=before):
        if (
            message.type == discord.MessageType.default
            or message.type == discord.MessageType.reply
        ):
            return message


def realize_pings(
    channel: discord.TextChannel,
    message_content: str,
    mentions: set[Union[discord.User, discord.Member]],
):
    for member in mentions:
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
        try:
            return yaml.safe_load(topic.split("---")[1]) or {}
        except Exception as e:
            print(f"Error parsing YAML in channel {channel.name}: {e}")
            return {}
    else:
        return {}


def parse_dot_command(message: discord.Message):
    match = re.match(r"^\.(\w+)(?:[\s|\.]+(.+))?$", message.content.split("---", 1)[0])
    if match:
        try:
            yaml_content = yaml.safe_load(message.content.split("---", 1)[-1])
        except Exception as e:
            print(f"Error parsing YAML")
            yaml_content = {}
        return {
            "command": match.group(1),
            "args": re.split("[\s|\.]", match.group(2)) if match.group(2) else [],
            "yaml": yaml_content or {},
        }
    else:
        return None


def compile_config_message(
    command_prefix: str = "config",
    config_dict: Optional[dict] = None,
    targets: Optional[list[discord.User] | str] = None,
):
    dict_copy = {k: v for k, v in config_dict.items() if v is not None}
    config_yaml = yaml.dump(dict_copy) if len(dict_copy) > 0 else ""
    config_message = f".{command_prefix}"
    if targets is not None:
        for target in targets:
            if target is not None:
                config_message = (
                    config_message
                    + f" {target.mention if isinstance(target, discord.User) else target}"
                )
    config_message = config_message + f"\n---\n{config_yaml}"
    return config_message


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
        match = re.match(r"^\.?(.+?)(?:[\s|-](.+?))?(?:\.(.+?))?$", attachment.filename)
        if match:
            try:
                att_info["yaml"] = yaml.safe_load(
                    await get_attachment_content(attachment)
                )
                att_info["command"] = match.group(1)
                att_info["args"] = (
                    re.split("[\s|-]", match.group(2)) if match.group(2) else []
                )
            except Exception as e:
                print(f"Error parsing YAML")
    return att_info


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


async def get_attachment_content(attachment: discord.Attachment) -> str:
    # @lru_cache doesn't work on async functions, so use this as a workaround
    return await asyncio.to_thread(get_attachment_content_inner, attachment)


@lru_cache
def get_attachment_content_inner(attachment: discord.Attachment):
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


def message_invisible(message: discord.Message, iface_config: DiscordInterfaceConfig):
    if is_continue_command(message.content):
        return True
    elif is_mu_command(message.content):
        return True
    elif iface_config.ignore_dotted_messages and (
        re.match(DOTTED_MESSAGE_RE, message.content)
        or message.type == discord.MessageType.thread_starter_message
        or message.type == discord.MessageType.thread_created
        or message.type == discord.MessageType.pins_add
        or message.type == discord.MessageType.channel_name_change
    ):
        return True
    return False


async def get_config_from_message(
    message: discord.Message,
    pov_config: Optional[Config] = None,
    pov_user: Optional[discord.User] = None,
):
    config = {}
    is_config_message = False
    dot_command = parse_dot_command(message)
    if dot_command:
        if dot_command["command"] == "config" and (
            len(dot_command["args"]) == 0
            or name_in_list(dot_command["args"], config=pov_config, user=pov_user)
            or pov_user.mentioned_in(message)
        ):
            config = dot_command["yaml"]
            is_config_message = True
    for attachment in message.attachments:
        att_data = await parse_attachment(attachment)
        if att_data["type"] == "text" and (
            len(att_data["args"]) == 0
            or name_in_list(att_data["args"], config=pov_config, user=pov_user)
            or pov_user.mentioned_in(message)
        ):
            if att_data["command"] == "config":
                config.update(att_data["yaml"])
                is_config_message = True
            elif is_config_message:
                config[att_data["command"]] = att_data["yaml"]
    return config


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

    return any(
        # name in name_list for name in (self.user.name, self.sysname, *nicknames)
        name in name_list
        for name in (user.name, config.em.emname, *nicknames)
    )


class ConfigError(ValueError):
    pass
