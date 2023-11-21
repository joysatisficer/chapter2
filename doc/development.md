# Development

## Building

1. Clone [embedapi](https://github.com/CrazyPython/embedapi) to `../embedapi` (relative to the root of Chapter 2)
2. Clone [intermodel](https://github.com/CrazyPython/intermodel) to ../modules/intermodel
3. Install dependencies

```bash
poetry install
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
