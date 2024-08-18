#!/usr/bin/env python3
"""Tool to import messages from Tyrrrz's Discord Chat Exporter tool"""
from chapter2.declarations import Message, Author, UserID
from chapter2.message_formats import IRCMessageFormat

import sys
import json

from datetime import datetime


def parse_date_to_unix_timestamp(date_str):
    # Format of the date string
    date_format = "%Y-%m-%dT%H:%M:%S.%f%z"
    # Parse the date string into a datetime object
    parsed_date = datetime.strptime(date_str, date_format)
    # Convert parsed date to UNIX timestamp
    unix_timestamp = parsed_date.timestamp()
    return unix_timestamp


section_flag = True
for fname in sys.argv[1:]:
    with open(fname) as f:
        jsonobj = json.load(f)

    irc = IRCMessageFormat()
    # todo: insert "---" based on timestamps
    prev = None
    if not section_flag:
        print("---")
        section_flag = True
    for message in jsonobj["messages"]:
        content = message["content"]
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
        section_flag = False
