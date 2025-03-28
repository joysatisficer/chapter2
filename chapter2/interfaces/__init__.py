from interfaces.chatcompletions_interface import ChatCompletionsInterface
from interfaces.completions_interface import CompletionsInterface
from interfaces.discord_interface import DiscordInterface
from interfaces.rpc_interface import RPCInterface
from interfaces.addons.discord_generate_avatar import discord_generate_avatar
from interfaces.infra_interface import InfraInterface

INTERFACE_NAME_TO_INTERFACE = {
    "discord": DiscordInterface,
    "rpc": RPCInterface,
    # compatible with OpenAI's API for text completion
    "completions": CompletionsInterface,
    # compatible with OpenAI's API for chat models
    "chatcompletions": ChatCompletionsInterface,
    "infra": InfraInterface,
}
INTERFACE_ADDON_NAME_TO_ADDON = {
    "discord": {
        "generate_avatar": discord_generate_avatar,
    },
}
