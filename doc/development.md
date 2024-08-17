# Development

## System services
### [`embed_server`](https://github.com/ampdot-io/embed_server)
Running your own local instance is recommended.

## Building

1. Install [Rye](https://rye.astral.sh/guide/installation/), a one-stop-shop for Python dependency management written in Rust. On POSIX systems, you can use:
```bash
curl -sSf https://rye.astral.sh/get | bash
```
2. Install dependencies with Rye
```bash
rye sync
```
3. Get nltk data
```pycon
>>> import nltk
>>> nltk.download('punkt')
```
4. Log into huggingface (required for using open-source models)
```bash
huggingface-cli login
```

## Running

```bash
./chapter2/main.py [em-name]
```

will run the em in `ems/[em-name]`

### REPL

#### Method 1

```py
from tools.repl import *
```

will import key Chapter 2 functions as libraries

### As a library
```py
from chapter2.message_formats import IRCMessageFormat
```
gets you Chapter 2's IRCMessageFormat

#### Method 2

```bash
./chapter2/main.py -- --interactive
```
or shorthand:
```bash
./chapter2/main.py -- -i
```

will activate a REPL with Chapter 2's libraries activated

## Running tests
Tests are unused

## Running type checking
Type-checking has not been set up

## Toolchain setup

### PyCharm
When receiving SIGINT, the process will wait until zero messages are being handled before exiting. Set
`kill.windows.processes.softly` inside IntelliJ Registry to have the "STOP" button do this.

Mark `src` as sources root to resolve import errors inside interfaces and faculties
