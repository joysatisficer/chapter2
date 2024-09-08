# Development

## Setup

1. Install [Rye](https://rye.astral.sh/guide/installation/), a one-stop-shop
for Python dependency management written in Rust. On POSIX systems, you can
use:
```bash
curl -sSf https://rye.astral.sh/get | bash
```
2. Install dependencies with Rye
```bash
rye sync
```
3. Log into huggingface (required for using open-source models)
You may need to agree to licenses to access gated models on huggingface.co in your web browser.
For example, [agree to the terms of service for LLaMa 3.1](https://huggingface.co/meta-llama/Meta-Llama-3.1-405B)
```bash
huggingface-cli login
```
4. Configure API keys
*This step is optional if you have OpenAI or Anthropic configured via environment 
variables. (OPENAI_API_KEY, OPENAI_API_BASE, ANTHROPIC_API_KEY)*

Elysium Conduit (hosted API, ask ampdot for a Tailscale invite link):
```
vendors:
  openai-conduit:
    config:
      openai_api_key: asdf
      api_base: https://conduit.elysium.mesh.host/v1
    provides: [".*"]
```

OpenRouter:
```
vendors:
  openai-openrouter:
    config:
      openai_api_key: YOUR_OPENROUTER_KEY_HERE
      api_base: https://openrouter.ai/api/v1
    provides: [".*"]
```
5. Set up `embed_server`
Download and install:
```bash
git clone git@github.com:ampdot-io/embed_server.git
cd embed_server
rye sync
```

Run:
```bash
rye run embed_server
```

## Running

(The entrypoint is `chapter2/main.py`)

```bash
rye run em [em-name]
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
rye run em -- --interactive
```
or shorthand:
```bash
rye run em -- -i
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
