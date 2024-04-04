#!/usr/bin/env -S python -u
import os
import signal
import sys
import asyncio
from pathlib import Path

import yaml

import resolve_config
from generate_response import generate_response
from interfaces import INTERFACE_NAME_TO_INTERFACE, INTERFACE_ADDON_NAME_TO_ADDON
from resolve_config import Config
from declarations import Message, UserID, Author
from interfaces.discord_interface import get_yaml_from_channel
from util.asyncutil import eager_iterable_to_async_iterable


def get_config_getter(bot_config: Config):
    # todo: config loaders as separate entities from interfaces
    async def get_config(channel: "discord.abc.MessageableChannel") -> Config:
        if isinstance(channel, dict):
            kv = channel
        elif channel is not None:
            kv = await get_yaml_from_channel(channel)
        else:
            kv = {}
        return resolve_config.load_config_from_kv(
            kv,
            bot_config.model_dump(),
        )

    return get_config


async def rehearse_em(config: Config):
    """Run an em in mock mode to populate caches and test the em"""
    mock_messages = [
        Message(Author("alice"), "hello"),
        Message(Author("bob"), "hi alice!"),
        Message(Author(config.name), "hi bob!"),
        Message(Author("alice"), f"hi {config.name}!"),
    ][::-1]
    async for response in generate_response(
        UserID("em::" + config.name, "rehearsal"),
        eager_iterable_to_async_iterable(mock_messages),
        config,
    ):
        pass


def load_em(name) -> Config:
    parent_dir = Path(__file__).resolve().parents[1]
    em_folder = parent_dir / "ems" / name
    with open(em_folder / "config.yaml") as file:
        kv = yaml.safe_load(file)
        if kv is None:
            kv = {}
    try:
        with open(os.path.expanduser("~/.config/chapter2/vendors.yaml")) as file:
            kv = {**kv, **yaml.safe_load(file)}
    except FileNotFoundError:
        pass
    try:
        with open(parent_dir / "ems/vendors.yaml") as file:
            kv = {**kv, **yaml.safe_load(file)}
    except FileNotFoundError:
        pass
    kv["em_folder"] = em_folder
    for subpath in em_folder.iterdir():
        if (
            subpath.name in Config.model_fields.keys()
            or subpath.name in resolve_config.ALIASES.keys()
        ):
            kv[subpath.name] = subpath.read_text()
    if "name" not in kv:
        kv["name"] = name
    # TODO: Replace with defaults versioning system
    if kv.get("legacy", False):
        defaults = resolve_config.LEGACY_DEFAULTS
        del kv["legacy"]
    else:
        defaults = resolve_config.DEFAULTS
    config = resolve_config.load_config_from_kv(kv, defaults)
    return config


async def run_em(name, end_to_end_test=False):
    config = load_em(name)
    if end_to_end_test:
        config.end_to_end_test = True
    if config.sentry_dsn_url is not None:
        setup_sentry(config)
    args = get_config_getter(config), generate_response, config.name
    interfaces = []
    for interface in config.interfaces:
        interface_name = interface.name
        addons = []
        if hasattr(interface, "addons"):
            for addon in interface.addons:
                addons.append(
                    INTERFACE_ADDON_NAME_TO_ADDON[interface_name][addon.name](addon)
                )
        base_interface = INTERFACE_NAME_TO_INTERFACE[interface_name]
        if len(addons) == 0:
            interfaces.append((base_interface, interface))
        else:
            interfaces.append(
                (
                    type(
                        "Custom" + base_interface.__name__,
                        (*addons, base_interface),
                        {},
                    ),
                    interface,
                )
            )

    interface_instances = []
    for interface, interface_config in interfaces:
        interface_instances.append(interface(*args, interface_config))

    def handle_interrupt(sig, frame):
        for interface_instance in interface_instances:
            interface_instance.stop(sig, frame)

    signal.signal(signal.SIGINT, handle_interrupt)

    await asyncio.gather(
        asyncio.create_task(
            rehearse_em(
                resolve_config.load_config_from_kv(
                    config.model_dump(), resolve_config.REHEARSAL_CONFIG
                )
            )
        ),
        *[interface_instance.start() for interface_instance in interface_instances],
    )

    exit_code = 0
    for interface_instance in interface_instances:
        if (
            hasattr(interface_instance, "end_to_end_test_fail")
            and interface_instance.end_to_end_test_fail
        ):
            exit_code = 1
    return exit_code


def setup_sentry(config: Config):
    import sentry_sdk, platform

    sentry_sdk.init(
        dsn=config.sentry_dsn_url,
        traces_sample_rate=1.0,
        profiles_sample_rate=1.0,
    )
    path = Path(config.em_folder)
    em = path.parts[-1]
    deployment = path.parts[-3]
    hostname = platform.node().split(".")[0].lower()
    sentry_sdk.set_tag("instance", f"{em}/{deployment}@{hostname}")


if __name__ == "__main__":
    from rich.traceback import install
    import fire
    import selectors

    install(suppress=[asyncio, fire, selectors])

    def _(name, end_to_end_test=False):
        result = asyncio.run(run_em(name, end_to_end_test))
        sys.exit(result)

    fire.Fire(_)
