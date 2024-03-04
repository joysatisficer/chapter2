from interfaces.chatcompletions_interface import ChatCompletionsInterface
from interfaces.completions_interface import CompletionsInterface
from interfaces.discord_interface import DiscordInterface
from interfaces.mikoto_interface import MikotoInterface
from interfaces.addons.discord_generate_avatar import discord_generate_avatar

INTERFACE_NAME_TO_INTERFACE = {
    "discord": DiscordInterface,
    # open-source messaging app
    "mikoto": MikotoInterface,
    # compatible with OpenAI's API for text completion
    "completions": CompletionsInterface,
    # compatible with OpenAI's API for chat models
    "chatcompletions": ChatCompletionsInterface,
}
INTERFACE_ADDON_NAME_TO_ADDON = {
    "discord": {
        "generate_avatar": discord_generate_avatar,
    },
}
