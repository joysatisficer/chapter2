import discord
from discord import app_commands
from discord.ui import Button, View
from typing import Optional
from pydantic import ValidationError
from io import StringIO
import yaml
from aioitertools.more_itertools import take as async_take

from interfaces.discord_interface import DiscordInterface, ConfigError
from util.asyncutil import async_generator_to_reusable_async_iterable
from util.discord_improved import parse_discord_content
import ontology
from ontology import Config, DiscordInterfaceConfig
from load import load_em_kv
from util.steering_api import INDEX_TO_DESC, USABLE_FEATURES
from util.app_info import get_emname_id_map, get_steerable_ems
from generate_response import get_prompt


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


class InfraInterface(DiscordInterface):
    """Stability: Alpha"""

    BLACKLISTED_KEYS = {
        "folder",
        "novelai_api_key",
        "exa_search_api_key",
        "vendors",
        "discord_token",
        "discord_proxy_url",
    }

    FUTURES_PREFIX = ".:twisted_rightwards_arrows: **futures**"
    ANCESTRY_PREFIX = ".:arrows_counterclockwise: **ancestry**:"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.tree = app_commands.CommandTree(self)
        self.emname_to_id = get_emname_id_map()
        self.id_to_emname = {v: k for k, v in self.emname_to_id.items()}
        self.steerable_ems = get_steerable_ems()
        self.discord_iface_config = DiscordInterfaceConfig(
            **{
                **self.iface_config.model_dump(),
                "name": "discord",
            }
        )

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
                use_application_commands=True,
            ),
        )

    async def on_message(self, message: discord.Message) -> None:
        if message.author == self.user:
            return
        elif self.message_invisible(message, self.discord_iface_config):
            return
        # if the message is in a thread and the name of the thread ends with "⌥"
        elif isinstance(
            message.channel, discord.threads.Thread
        ) and message.channel.name.endswith("⌥"):
            parent = await last_normal_message(message.channel, before=message)
            if (
                parent
                and not self.message_invisible(parent, self.discord_iface_config)
                and parent.author == message.author
            ):
                return
            name = "⌥ " + self.message_preview_text(
                message, max_length=40, styled=False
            )
            await message.channel.edit(name=name)
            await self.update_index_pointer(message)

        return

    async def update_index_pointer(self, message: discord.Message):
        parent_node = await self.get_parent_node(message)
        if parent_node is not None:
            _, index_msg, _, _, _ = await self.get_loom_index(parent_node)
            if index_msg is not None:
                _, children_links = parse_index_message(index_msg)
                if children_links is not None:
                    new_children_links = []
                    for child in children_links:
                        if isinstance(child, dict):
                            new_children_links.append(child["link"])
                        else:
                            new_children_links.append(child)
                    for child_link in new_children_links:
                        if child_link == message.channel.jump_url:
                            new_children_links.remove(child_link)
                            new_children_links.append(message.jump_url)
                            view, index_msg_content = await self.futures_message(
                                parent_node, new_children_links
                            )
                            await index_msg.edit(content=index_msg_content, view=view)
                            return

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
        if emname == self.sysname:
            emname = None
        config, iface_config = (
            await load_em_configs(emname)
            if emname is not None
            else (self.base_config, self.discord_iface_config)
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
                print(f"Error getting config: {exc}")
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
                f"**⚠ ERROR** handling **{command_name}** command",
            )

    async def setup_hook(self):
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

        @self.event
        async def on_interaction(interaction: discord.Interaction):
            if not interaction.type == discord.InteractionType.component:
                return

            try:
                custom_id = interaction.data.get("custom_id", "")
                if custom_id.startswith("fork_button|"):

                    _, message_url, is_public = custom_id.split("|")
                    message = await self.get_message_from_link(
                        reconstruct_link(message_url)
                    )
                    is_public = is_public.lower() == "true"
                    # Handle the button click
                    await self.fork_to_thread_callback(
                        interaction=interaction,
                        message=message,
                        ephemeral=not is_public,
                        public=is_public,
                    )
                # elif custom_id.startswith("select_menu|"):
                # values = interaction.data.get("values", [])
                # if not values:
                #     return

                # # Parse the custom_id to get message info
                # _, message_url = custom_id.split("|")

                # Handle the selection
                # await interaction.response.send_message(
                #     f"You selected option: {values[0]}",
                #     ephemeral=True
                # )

                else:
                    return
            except Exception as e:
                print(f"Error handling button interaction: {e}")

        async def em_users_autocomplete(interaction: discord.Interaction, current: str):
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

        async def feature_autocomplete(interaction: discord.Interaction, current: str):
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
                current_configuration = config_dict["continuation_options"]["steering"][
                    "feature_levels"
                ]
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

        async def targets_autocomplete(interaction: discord.Interaction, current: str):
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
            all_keys = all_keys - self.BLACKLISTED_KEYS
            matches = [key for key in all_keys if current.lower() in key.lower()]
            return [app_commands.Choice(name=key, value=key) for key in matches[:25]]

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
                name="get_prompt", description="send the prompt of a message"
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
                    command_name="/get_prompt",
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
                name="config_speakers",
                description="update configuration for may_speak (pins .config message)",
            )
            @app_commands.autocomplete(may_speak=targets_autocomplete)
            @app_commands.describe(
                may_speak="space-separated list of ems to allow to speak (if not set, allow all ems)",
            )
            async def configure_speakers(
                interaction: discord.Interaction,
                may_speak: Optional[str] = None,
            ):
                if may_speak is not None:
                    may_speak = may_speak.split(" ")
                else:
                    may_speak = []
                await self.interaction_wrapper(
                    command_name="/config_speakers",
                    func=self.send_config_command,
                    interaction=interaction,
                    command_prefix="config",
                    config_dict={
                        "may_speak": may_speak,
                    },
                    targets=None,
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
                include_images: Optional[bool] = None,
                mute: Optional[bool] = None,
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
                name="unset_config",
                description="unset/reset/clear configuration for channel (unpins all .config messages)",
            )
            async def unset_config(
                interaction: discord.Interaction,
            ):
                await self.interaction_wrapper(
                    command_name="/unset_config",
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

            @self.tree.command(
                name="get_ancestry",
                description="get the loom ancestry of a message",
            )
            async def get_ancestry(
                interaction: discord.Interaction,
                message_link: Optional[str] = None,
                public: bool = False,
            ):
                await self.interaction_wrapper(
                    command_name="/get_ancestry",
                    func=self.get_ancestry_command,
                    interaction=interaction,
                    message_link=message_link,
                    public=public,
                )

            if len(self.steerable_ems) > 0:

                @self.tree.command(
                    name="set_feature",
                    description="configure Claude 3 Sonnet steering feature",
                )
                @app_commands.autocomplete(target=steerable_users_autocomplete)
                @app_commands.autocomplete(feature=feature_autocomplete)
                @app_commands.describe(
                    target="em to configure the steering feature of",
                    feature="feature to configure",
                    level="feature level(-10 to 10)",
                    reset="(FALSE by default) reset previously configured features",
                )
                async def set_feature(
                    interaction: discord.Interaction,
                    target: str,
                    feature: str,
                    level: float,
                    # level: Optional[float] = None,
                    reset: bool = False,
                ):
                    await self.interaction_wrapper(
                        command_name="/set_feature",
                        func=self.config_steering_feature_command,
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
                    name="unset_feature",
                    description="unset/reset/clear a steering feature",
                )
                @app_commands.autocomplete(feature=current_features_autocomplete)
                @app_commands.autocomplete(target=steerable_users_autocomplete)
                @app_commands.describe(
                    target="em to unset the steering feature of",
                    feature="feature to unset",
                )
                async def unset_feature(
                    interaction: discord.Interaction,
                    target: str,
                    feature: str,
                ):
                    await self.interaction_wrapper(
                        command_name="/unset_feature",
                        func=self.config_steering_feature_command,
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
                    name="unset_features",
                    description="unset/reset/clear all steering features",
                )
                @app_commands.autocomplete(target=steerable_users_autocomplete)
                @app_commands.describe(
                    target="em to unset the steering features of",
                )
                async def unset_features(
                    interaction: discord.Interaction,
                    target: str,
                ):
                    config_dict = {
                        "continuation_options": {"steering": {"feature_levels": {}}}
                    }
                    await self.interaction_wrapper(
                        command_name="/unset_features",
                        func=self.send_config_command,
                        interaction=interaction,
                        command_prefix="config",
                        config_dict=config_dict,
                        targets=[target],
                    )

                @self.tree.command(
                    name="get_features",
                    description="show current steering state",
                )
                @app_commands.autocomplete(pov=steerable_users_autocomplete)
                @app_commands.describe(
                    pov="em to show the steering features configuration of",
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
                        command_name="/get_features",
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

            async def private_fork_menu_command(
                interaction: discord.Interaction, message: discord.Message
            ):
                await self.interaction_wrapper(
                    command_name="/fork",
                    func=self.fork_command,
                    interaction=interaction,
                    message=message,
                    public=False,
                    title=None,
                )

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

            create_private_fork_command = app_commands.ContextMenu(
                name="fork (private)",
                callback=private_fork_menu_command,
                type=discord.AppCommandType.message,
            )

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
            self.tree.add_command(create_private_fork_command)
            self.tree.add_command(create_public_fork_command)
            self.tree.add_command(mu_command)

        except Exception as e:
            print(f"Error registering context menu command: {e}")
            exit(1)

        sync_result = await self.tree.sync()

        print(
            f"Sync completed. Registered {len(sync_result)} commands: {[cmd.name for cmd in sync_result]}"
        )
        await super().setup_hook()

    async def send_config_command(self, **kwargs):
        interaction = kwargs["interaction"]
        command_prefix = kwargs.get("command_prefix", ".config")
        config_dict = kwargs.get("config_dict", None)
        targets = kwargs.get("targets", None)
        config_message = compile_config_message(
            command_prefix,
            config_dict,
            targets,
        )
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
            content = f"✓ unpinned {len(unpinned_messages)} config messages"
            # for message in unpinned_messages:
            #     content += f"\n- {message.jump_url}"
        else:
            content = "✗ no config messages to unpin"
        await interaction.followup.send(content)

    async def config_steering_feature_command(self, **kwargs):
        config = kwargs["config"]
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
        if level is not None:
            config_dict["continuation_options"]["steering"]["feature_levels"][
                feature_key
            ] = level
        else:
            del config_dict["continuation_options"]["steering"]["feature_levels"][
                feature_key
            ]

        # check if sum of absolute values of all feature levels is greater than 10
        if (
            sum(
                abs(level)
                for level in config_dict["continuation_options"]["steering"][
                    "feature_levels"
                ].values()
            )
            > 10
        ):
            raise ValueError(
                "Sum of absolute values of feature levels must be no greater than 10.\nUse **/get_features** to see current steering state and **/unset_features** to reset steering state."
            )

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
            content = f"✗ no steering features configured for {pov_user.mention}"
        else:
            content = f"### :information_source: current steering features for {pov_user.mention}:\n"
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
        # message = kwargs["message"]
        config = kwargs["config"]
        pov = kwargs["pov"]
        pov_user = kwargs["pov_user"]

        property = kwargs.get("property", None)
        cleaned_config = clean_config_dict(
            config.model_dump(),
            list(self.BLACKLISTED_KEYS),
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

    async def get_ancestry_command(self, **kwargs):
        interaction = kwargs["interaction"]
        message = kwargs["message"]
        ancestry = await self.get_node_ancestry(message)
        ancestry = reversed(ancestry)
        ancestry_string = self.ANCESTRY_PREFIX
        ancestry_string += format_ancestry_chain(ancestry, None, message)
        ancestry_string += f" :point_left:"
        await interaction.followup.send(ancestry_string)

    async def get_context_command(
        self,
        **kwargs,
    ):
        interaction = kwargs["interaction"]
        message = kwargs["message"]
        config = kwargs["config"]
        iface_config = kwargs["iface_config"]
        # pov = kwargs["pov"]
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

        prompt = await get_prompt(config.em, history)

        file = discord.File(
            StringIO(prompt),
            filename=f"prompt-{pov_user.name}-{message.id}.txt",
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

        iface_config = self.discord_iface_config
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

        return await self.fork_to_thread_callback(
            interaction=interaction,
            message=message,
            ephemeral=not public,
            public=public,
            title=title,
        )

    async def mu_command(self, **kwargs):
        message = kwargs["message"]
        parent_message = await last_normal_message(message.channel, before=message)
        if parent_message is None:
            raise ValueError("No parent message found")
        kwargs["message"] = parent_message
        new_thread = await self.fork_command(**kwargs)
        message_author = message.author

        await new_thread.send(f"m continue {message_author.mention}")
        # return

    async def fork_to_thread_callback(
        self,
        interaction: discord.Interaction,
        message: discord.Message,
        ephemeral: bool = True,
        public: bool = True,
        title: Optional[str] = None,
    ):
        # Only defer if the interaction hasn't been responded to
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=ephemeral)

        reason = f"Created by {interaction.user}" + (
            f" through {interaction.command.name}"
            if interaction.command
            else " through button click"
        )

        new_thread, ancestry_message, index_message = await self.fork_to_thread(
            message=message,
            reason=reason,
            public=public,
            title=title,
            interaction=interaction,
        )
        emoji = "✓" if public else "✓ :lock:"
        fork_message = f".{emoji} **created fork:** {message.jump_url} ⌥ {ancestry_message.jump_url}"
        interaction_in_index = (
            index_message is not None
            and interaction.channel.id == index_message.channel.id
        )
        if index_message is not None and not interaction_in_index:
            fork_message += f"\n[(see in loom index)]({index_message.jump_url})"

        if not interaction_in_index:
            view = discord.ui.View(timeout=None)
            view.add_item(
                discord.ui.Button(
                    style=discord.ButtonStyle.primary,
                    label="+⌥",
                    custom_id=f"fork_button|{min_link(message.jump_url)}|{public}",
                )
            )
            await interaction.followup.send(
                fork_message,
                ephemeral=ephemeral,
                view=view,
            )
        else:
            await interaction.followup.send(
                fork_message,
                ephemeral=True,
            )
        return new_thread

    async def fork_to_thread(
        self,
        interaction: discord.Interaction,
        message: discord.Message,
        title: Optional[str] = None,
        reason: str = "Created by infra",
        public: bool = False,
    ):
        if title is None:
            title = (
                self.message_preview_text(
                    message, max_length=20, anchor_at_end=True, include_sender=False
                )
                + "⌥"
            )

        index_thread, index_msg, thread_channel, ancestors, ancestor_index_messages = (
            await self.get_loom_index(message)
        )

        if thread_channel and index_thread is None and public:
            # if index thread does not exist, create it
            index_thread_name = (
                "[LOOM INDEX] "
                + self.message_preview_text(
                    ancestors[-1],
                    max_length=20,
                    anchor_at_end=True,
                    include_sender=False,
                )
                + "⌥*"
            )
            index_thread = await ancestors[-1].create_thread(
                name=index_thread_name,
                reason=reason,
            )

        new_thread = await thread_channel.create_thread(name=title, reason=reason)
        embed = embed_from_message(message)
        ancestry_message_content = (
            self.ANCESTRY_PREFIX
            + format_ancestry_chain(
                reversed(ancestors), ancestor_index_messages, message
            )
            + "**⌥** ((:eye:))"
        )

        reference = None
        if len(ancestors) > 1 and ancestors[1].jump_url in ancestor_index_messages:
            reference = await self.get_message_from_link(
                ancestor_index_messages[ancestors[1].jump_url]
            )

        new_history_message = await new_thread.send(
            content=compile_config_message(
                command_prefix="history",
                config_dict={"last": message.jump_url},
            ),
        )

        new_ancestry_message = await new_thread.send(
            content=ancestry_message_content,
            embed=embed,
        )

        if not public:
            # send a message to the new private thread pinging the user to add them
            await new_thread.send(f".{interaction.user.mention}")
        elif index_thread is not None:
            if index_msg is None:

                index_ancestry_message = await index_thread.send(
                    content=ancestry_message_content,
                    embed=embed,
                    reference=reference,
                )

                next_message = None
                async for next_msg in message.channel.history(
                    after=message, oldest_first=True, limit=3
                ):
                    # async for next_msg in self.cache(message.channel).history(after=message, limit=3):
                    if not next_msg.is_system() and not next_msg.author == self.user:
                        next_message = next_msg
                        break

                children_links = (
                    [next_message.jump_url] if next_message is not None else []
                )
                children_links.append(new_thread.jump_url)

                view, index_msg_content = await self.futures_message(
                    message, children_links, public
                )
                index_msg = await index_thread.send(
                    content=index_msg_content,
                    view=view,
                )
            else:
                root_link, children_links = parse_index_message(index_msg)
                if root_link is None:
                    raise ValueError("Error parsing index message")
                    # new_future_content = f"\n- {new_ancestry_message.jump_url}"
                    # await index_msg.edit(
                    #     content=index_msg.content + new_future_content
                    # )
                elif children_links is not None:
                    children_links.append(new_thread.jump_url)
                    view, index_msg_content = await self.futures_message(
                        message, children_links, public
                    )
                    await index_msg.edit(content=index_msg_content, view=view)

            embed.description = (
                message.content
                + f"\n\n-# [:twisted_rightwards_arrows: alt futures]({index_msg.jump_url})"
            )
            await new_ancestry_message.edit(embed=embed)

        return new_thread, new_ancestry_message, index_msg

    async def futures_message(
        self,
        message: discord.Message,
        children_links: list[str | dict],
        public: bool = True,
    ):

        view = discord.ui.View(timeout=None)

        view.add_item(
            discord.ui.Button(
                style=discord.ButtonStyle.primary,
                label="+⌥",
                custom_id=f"fork_button|{min_link(message.jump_url)}|{public}",
            )
        )

        child_options = []
        if len(children_links) > 0:
            child_options = await self.make_child_options(children_links)

            view.add_item(
                discord.ui.Select(
                    custom_id=f"select_menu|{min_link(message.jump_url)}",
                    placeholder="Browse futures",
                    options=child_options,
                )
            )

        index_tree = {message.jump_url: child_options}

        index_msg_content = await self.format_futures(index_tree)

        return view, index_msg_content

    async def get_loom_index(self, message: discord.Message):
        ancestors = await self.get_node_ancestry(message)
        thread_channel = None
        index_thread = None
        index_msg = None
        ancestor_index_messages = {}

        if not isinstance(ancestors[-1].channel, discord.threads.Thread):
            thread_channel = ancestors[-1].channel
            index_thread = await self.get_thread_from_message(ancestors[-1])
            if index_thread is not None:
                index_msg, ancestor_index_messages = await self.get_index_message(
                    message, index_thread, ancestors
                )
        else:
            thread_channel = message.channel.parent

        return (
            index_thread,
            index_msg,
            thread_channel,
            ancestors,
            ancestor_index_messages,
        )

    async def get_index_message(
        self,
        message: discord.Message,
        index_thread: discord.Thread,
        ancestors: list[discord.Message] | None = None,
    ):
        index_msg = index_thread.starter_message
        ancestor_links = (
            [ancestor.jump_url for ancestor in ancestors]
            if ancestors is not None
            else []
        )
        ancestor_index_messages = {}
        async for next_msg in index_thread.history(
            after=index_msg, oldest_first=True, limit=None
        ):
            # async for next_msg in self.cache(index_thread).history(after=index_msg, limit=None):
            index_msg = next_msg
            try:
                root_link, children = parse_index_message(index_msg)
            except StopIteration:
                pass
            if root_link == message.jump_url:
                break
            if root_link in ancestor_links:
                ancestor_index_messages[root_link] = index_msg.jump_url
        else:
            index_msg = None
        return index_msg, ancestor_index_messages

    async def get_parent_node(self, message: discord.Message):
        message_thread = message.channel
        if not isinstance(message_thread, discord.threads.Thread):
            return None
        async for msg in message_thread.history(
            after=message_thread.starter_message, oldest_first=True
        ):
            # async for msg in self.cache(message.channel).history(after=message, limit=None):
            if msg is not None and msg.content.startswith(".history"):
                history_config = self.parse_dot_command(msg)
                if history_config and history_config["yaml"]:
                    parent_message = await self.get_message_from_link(
                        history_config["yaml"]["last"]
                    )
                    return parent_message
        return None

    async def get_node_ancestry(self, message: discord.Message):
        ancestry = []
        current_message = message
        while current_message is not None:
            ancestry.append(current_message)
            current_message = await self.get_parent_node(current_message)
        return ancestry

    async def format_futures(self, tree: dict):
        futures_string = self.FUTURES_PREFIX
        root_link, children = next(iter(tree.items()))
        futures_string += f" of {root_link}:"
        for child in children:
            link = f"\n- {child}"
            description = ""
            if isinstance(child, dict):
                link = child["link"]
                description = child["description"]
            elif isinstance(child, discord.SelectOption):
                link = reconstruct_link(child.value)
                description = child.description
            futures_string += f"\n- {link}"
            if description is not None and description != "":
                futures_string += f"\n> -# {description}"

        return futures_string

    async def make_child_options(self, children_links: list[str | dict]):
        child_options = []
        for child_link in children_links:
            if isinstance(child_link, dict):
                child_link = child_link["link"]
            url_parts = child_link.split("channels/")[1].split("/")
            description = ""
            label = url_parts[-1]
            # check if link is a channel or message link
            if len(url_parts) == 3:
                child_message = await self.get_message_from_link(child_link)
                label = child_message.channel.name
                if (
                    not child_message.author == self.user
                    and not child_message.is_system()
                ):
                    description = self.message_preview_text(
                        child_message, max_length=80, styled=False, include_sender=False
                    )
            child_options.append(
                discord.SelectOption(
                    label=label, value=min_link(child_link), description=description
                )
            )
        return child_options

    def message_preview_text(
        self,
        message: discord.Message,
        max_length: int = 80,
        styled: bool = False,
        anchor_at_end: bool = False,
        include_sender: bool = True,
    ):
        if include_sender:
            message_sender = (
                f"**{message.author.name}:** " if styled else f"{message.author.name}: "
            )
        else:
            message_sender = ""
        parsed_content = parse_discord_content(
            message, self.user.id, self.user.name
        ).strip()
        split_content = parsed_content.split("\n")
        message_content_preview = (
            split_content[-1][-max_length:]
            if anchor_at_end
            else split_content[0][:max_length]
        )
        if len(parsed_content) > max_length:
            if anchor_at_end:
                message_content_preview = "..." + message_content_preview
            else:
                message_content_preview += "..."
        return message_sender + message_content_preview


def min_link(link: str):
    return link.split("channels/")[1]


def reconstruct_link(link: str):
    return f"https://discord.com/channels/{link}"


def get_select(message: discord.Message) -> Optional[discord.SelectMenu]:
    for row in message.components:
        for component in row.children:
            if isinstance(component, discord.SelectMenu):
                return component
    return None


def parse_index_message(message: discord.Message):
    select = get_select(message)
    if select is not None:
        root_link = reconstruct_link(select.custom_id.split("|")[1])
        children = [
            {"link": reconstruct_link(option.value), "description": option.description}
            for option in select.options
        ]
        return root_link, children
    if not message.content.startswith(InfraInterface.FUTURES_PREFIX):
        return None, None
    parts = message.content.split("of")
    if len(parts) < 2:
        return None, None
    tree_text = parts[1].strip()
    if not tree_text == "":
        tree = yaml.safe_load(tree_text)
        try:
            root_link, children = next(iter(tree.items()))
        except StopIteration:
            pass
        return root_link, children
    return None, None


def format_ancestry_chain(
    ancestry: list[discord.Message],
    ancestor_index_messages: dict | None = None,
    current_message: discord.Message | None = None,
):
    ancestry_string = ""
    indent = ""

    for message in ancestry:
        ancestry_string += f"\n{indent}- "
        if current_message is not None and message.jump_url == current_message.jump_url:
            ancestry_string += f"**⌥**{message.jump_url}"
        else:
            ancestry_string += "-# "
            ancestry_string += f"⌥{message.jump_url}"
        if (
            ancestor_index_messages is not None
            and message.jump_url in ancestor_index_messages
        ):
            ancestry_string += f"[⌥]({ancestor_index_messages[message.jump_url]})"
        indent += "  "
    return ancestry_string


def embed_from_message(message: discord.Message, timestamp: bool = False):
    embed = discord.Embed(description=message.content)
    embed.set_author(
        name=message.author.name,
        icon_url=message.author.display_avatar.url,
        url=message.jump_url,
    )
    if timestamp:
        embed.set_footer(text=message.created_at.strftime("%Y-%m-%d %H:%M:%S"))
    return embed


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
    config_message = config_message + "\n---\n" + config_yaml
    return config_message
