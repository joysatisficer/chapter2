#!/usr/bin/env python3
"""Tool to import messages from Tyrrrz's Discord Chat Exporter tool"""
from chapter2.declarations import Message, Author, UserID
from chapter2.message_formats import IRCMessageFormat

import sys
import json

for fname in sys.argv[1:]:
    with open(fname) as f:
        jsonobj = json.load(f)

    irc = IRCMessageFormat()
    for message in jsonobj["messages"]:
        print(
            irc.render(
                Message(
                    Author(
                        message["author"]["name"],
                        UserID(message["author"]["id"], "discord"),
                    ),
                    message["content"],
                )
            ),
            end="",
        )
