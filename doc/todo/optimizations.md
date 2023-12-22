# Optimizations

## One shared aiohttp ClientSession for everything to pool connections
- Pass to OpenAI
- Rewrite Metaphor client to use it
- Provider API that also abstracts file I/O
