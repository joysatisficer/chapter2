# Coding Guidelines
- make pitfalls and essential complexity obvious
- keep things short– spreading essential complexity over many lines does not simplify things
  - but also avoid DRY (Don't Repeat Yourself)– slightly duplicated code is better than complicating each callsite
- the measure of code quality is how easy something is to modify: avoid creating abstractions before they're needed or used
- write code as if it was being read by an AI

## Principles
- Sensible defaults with the ability to disable and customize features as appropriate

## Style
- Put high-level functions first because Python supports forward declarations. For example, DiscordInterface uses ChannelCache, so DiscordInterface goes before ChannelCache
