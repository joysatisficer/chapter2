#!/usr/bin/env -S python -u
import os
import signal
import sys
import asyncio
from pathlib import Path

import yaml

import ontology
from pydantic import Secret, SecretStr
from generate_response import generate_response
from interfaces import INTERFACE_NAME_TO_INTERFACE, INTERFACE_ADDON_NAME_TO_ADDON
from chapter2.interfaces.deserves_reply import deserves_reply
from ontology import Config
from declarations import Message, Author
from chapter2.util.asyncutil import to_async_iterable
from load import load_em


async def rehearse_em(config: Config):
    """Run an em in mock mode to populate caches and test the em"""
    mock_message_hist = to_async_iterable(
        [
            Message(Author("alice"), "hello"),
            Message(Author("bob"), "hi alice!\nhow are you?"),
            Message(Author(config.em.name), "hi bob!"),
            Message(Author("alice"), f"hi {config.em.name}!"),
        ][::-1]
    )
    config.em.vendors = Secret(
        {"fake-local": ontology.SingleVendorConfig(provides=[".*"])}
    )
    config.em.exa_search_api_key = SecretStr("sk-rehearsal")
    for interface in config.interfaces:
        if getattr(interface, "reply_on_sim", False):
            d = await deserves_reply(
                generate_response,
                config,
                mock_message_hist,
                interface.reply_on_sim,
            )
            assert isinstance(d, bool)
    async for response in generate_response(
        config.em,
        mock_message_hist,
    ):
        pass


async def run_em(name, end_to_end_test=False):
    config = load_em(name)
    interfaces = []
    for interface in config.interfaces:
        if end_to_end_test and hasattr(interface, "end_to_end_test"):
            interface.end_to_end_test = True
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
        interface_instances.append(
            interface(config, generate_response, name, interface_config)
        )

    def handle_interrupt(sig, frame):
        for interface_instance in interface_instances:
            interface_instance.stop(sig, frame)

    signal.signal(signal.SIGINT, handle_interrupt)

    await asyncio.gather(
        asyncio.create_task(rehearse_em(config.model_copy(deep=True))),
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


if __name__ == "__main__":
    from rich.traceback import install
    import fire
    import selectors

    if "PYCHARM_HOSTED" not in os.environ:
        install(suppress=(asyncio, fire, selectors), show_locals=True)

    def _(name, end_to_end_test=False):
        result = asyncio.run(run_em(name, end_to_end_test))
        sys.exit(result)

    fire.Fire(_)
