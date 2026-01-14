#!/usr/bin/env python3
"""Tool to import messages from Tyrrrz's Discord Chat Exporter tool"""

import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from chapter2.declarations import Author, Message
from chapter2.message_formats import IRCMessageFormat


def parse_date_to_unix_timestamp(date_str):
    return datetime.fromisoformat(date_str.replace("Z", "+00:00")).timestamp()


def process_messages(jsonobj):
    prev_timestamp = None
    for message, current_timestamp in sorted(
        (
            (m, parse_date_to_unix_timestamp(m["timestamp"]))
            for m in jsonobj.get("messages", [])
        ),
        key=lambda x: x[1],
    ):
        if prev_timestamp is not None and (current_timestamp - prev_timestamp) > 3600:
            yield "---"
        yield Message(
            Author(message["author"]["name"]),
            message["content"],
            timestamp=current_timestamp,
        )
        prev_timestamp = current_timestamp


def import_to_character(character_name, input_files):
    irc = IRCMessageFormat()
    character_dir = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..",
        "ems",
        character_name,
        f"{character_name}.chr",
    )
    os.makedirs(character_dir, exist_ok=True)
    for fname in input_files:
        with open(fname) as f:
            jsonobj = json.load(f)
        output_path = os.path.join(
            character_dir, os.path.splitext(os.path.basename(fname))[0] + ".txt"
        )
        with open(output_path, "w") as outf:
            for item in process_messages(jsonobj):
                if item == "---":
                    outf.write("---\n")
                else:
                    outf.write(irc.render(item))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import Discord messages to character")
    parser.add_argument("character_name", help="Name of the character")
    parser.add_argument("files", nargs="+", help="Input JSON files")
    args = parser.parse_args()
    import_to_character(args.character_name, args.files)
