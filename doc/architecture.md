# some general notes on architecture

Chapter 2 is lazy and purely functional.
It's ok to have some temporary inefficiency for the sake of better code organization.

When an em is started, it performs rehearsal (`main.py:rehearse_em`), a type of automated self-test where the em is run against mocks. This has two purposes: to perform a dry run and verify everything works, and to populate caches.

Complex ML dependencies are implemented separately in external processes.
Caching happens process-side. `embed_server` is a good example.

Managing API keys. chapter2 follows [vendors.yaml](https://github.com/ampdot-io/vendors.yml) looks for `intermodel.callgpt` API keys `~/.config/chapter2/vendors.yaml`, `ems/vendors.yaml`, and the `vendors` parameter of the em.

Prompt sections (ensembles) are produced by faculties. See `declarations:Faculty` for the type.

