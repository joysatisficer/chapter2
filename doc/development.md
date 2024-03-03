# Development

## System services
### [`embed_server`](https://github.com/CrazyPython/embed_server)
You can run it yourself or you can use my instance of it, just ask.

## Building

1. Clone [embedapi](https://github.com/CrazyPython/embedapi) to `../embedapi` (relative to the root of Chapter 2)
2. Clone [intermodel](https://github.com/CrazyPython/intermodel) to ../modules/intermodel
3. Install dependencies
```bash
poetry env use python3.11
poetry install
```
4. Get nltk data
```pycon
>>> import nltk
>>> nltk.download('punkt')
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
