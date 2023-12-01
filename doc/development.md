# Development

## System services
### [`embed_server`](https://github.com/ampdot-io/embed_server)
You can run it yourself or you can use my instance of it, just ask.

## Building

1. Clone [embedapi](https://github.com/ampdot-io/embedapi) to `../embedapi` (relative to the root of Chapter 2)
2. Clone [intermodel](https://github.com/ampdot-io/intermodel) to ../modules/intermodel
3. Install dependencies
```bash
poetry env use python3.10
poetry install
```
4. Get nltk data
```pycon
>>> import nltk
>>> nltk.download('punkt')
```

## Running

```bash
./src/main.py [em-name]
```

will run the em in `ems/[em-name]`

## Running tests
Tests are unused

## Running type checking
Type-checking has not been set up

## Toolchain setup

When receiving SIGINT, the process will wait until zero messages are being handled before exiting. Set
`kill.windows.processes.softly` inside IntelliJ Registry to have the "STOP" button do this.
