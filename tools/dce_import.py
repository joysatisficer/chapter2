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


def process_messages(jsonobj, after_timestamp=None, required_user=None):
    prev_timestamp = None
    messages = jsonobj["messages"]
    if after_timestamp is not None:
        messages = [
            m
            for m in messages
            if parse_date_to_unix_timestamp(m["timestamp"]) >= after_timestamp
        ]

    # Build chunks
    chunks = []
    current_chunk = []
    for message in sorted(
        messages, key=lambda m: parse_date_to_unix_timestamp(m["timestamp"])
    ):
        current_timestamp = parse_date_to_unix_timestamp(message["timestamp"])

        if prev_timestamp is not None and (current_timestamp - prev_timestamp) > 3600:
            if current_chunk:
                chunks.append(current_chunk)
                current_chunk = []

        current_chunk.append((message, current_timestamp))
        prev_timestamp = current_timestamp

    if current_chunk:
        chunks.append(current_chunk)

    # Filter chunks and yield messages
    for chunk in chunks:
        if required_user:
            has_user = any(
                msg["author"]["id"] == required_user
                or msg["author"]["name"] == required_user
                for msg, _ in chunk
            )
            if not has_user:
                continue

        yield "---"
        for message, current_timestamp in chunk:
            yield Message(
                Author(message["author"]["name"]),
                message["content"],
                timestamp=current_timestamp,
            )


def import_to_character(
    character_name, input_files, after_timestamp=None, required_user=None
):
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
            first = True
            chunk_marker_sent = False
            for item in process_messages(jsonobj, after_timestamp, required_user):
                if item == "---":
                    if not first:
                        outf.write("---\n")
                    first = False
                    chunk_marker_sent = True
                else:
                    outf.write(irc.render(item))
            if not chunk_marker_sent:
                outf.write("---\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import Discord messages to character")
    parser.add_argument("character_name", help="Name of the character")
    parser.add_argument("files", nargs="+", help="Input JSON files")
    parser.add_argument(
        "--after", help="Only include messages after this date (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--require-user",
        help="Only include chunks that contain this user ID or username",
    )
    args = parser.parse_args()
    after_timestamp = None
    if args.after:
        after_timestamp = datetime.strptime(args.after, "%Y-%m-%d").timestamp()
    import_to_character(
        args.character_name, args.files, after_timestamp, args.require_user
    )
