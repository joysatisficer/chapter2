# Chapter 2
Document status: Draft

Chapter 2 is a flexible ontology / type system for constructing language model simulacra, often for characters, known as ems.

It is made of highly decoupled components that work together very well. It is a set of simple primitives assembled by `Config` and `main.py` into a functioning em.

Chapter 2 is composable, remixable, and extensible and usable as a library. It is a [short program](https://cyborgism.wiki/hypha/short_program).

## Installation
See `doc/development.md`

## Architecture
### Config
`main.py` loads ems from the "ems" folder. An em needs a `config.yaml` file, which defines its configuration. The configuration keys are defined in `./chapter2/resolve_config.py`. 

Configuration keys can be written as files within that directory. For instance, you can set an em's `discord_token` by putting a file named `discord_token` within its folder.

The character faculty reads files from `*.txt` files in the `{config.name}.chr` subdirectory of the em folder.

### Core
A faculty is a function that takes the message history ensemble[^1], their faculty's configuration, and the global configuration produce ensembles. An ensemble is a lazy, asynchronous iterator of messages[^2], so faculties only pull data as necessary.

A message is a data structure that represents its author, contents, and timestamp.

Message formats convert messages to strings.

A Chapter 2 prompt is made by concatenating[^3] ensembles. You can control what separates different ensembles by configuring a faculty's[^4] `header` and `footer`. The separator for text inside an ensemble is defined by `separator`.

See `./chapter2/faculties/__init__.py` for a list of faculties.

Mufflers filter messages.

[^1]: Faculties will be able to take any ensemble as input, instead of just the message history ensemble. It will be possible to nest faculties inside each other, composing them arbitrarily.
[^2]: Chapter 2 will support more types of structured data than Message
[^3]: More operators than concatenation will be supported
[^4]: Currently the only way to define ensembles is with faculties, but more methods will be supported.

### Interfaces
An em can have interfaces. The default is one Discord interface. These are currently available:

See `./chapter2/interfaces/__init__.py` for a list of interfaces.

Interfaces can have add-ons. An add-on is a mix-in returned by a closure when passed the add-on configuration. Currently, the only available add-on is `generate_avatar` for the `discord` interface, which generates an avatar for a Discord bot using a prompt, and can optionally change the avatar at an interval.

## Running

See `doc/development.md`

## Pitfalls

- pytest unittests in files of the form `test_*.py` are broken
