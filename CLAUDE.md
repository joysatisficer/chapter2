# Chapter 2 Development Guide

## Commands
- Run an em: `rye run em [em-name]`
- Start REPL: `rye run em -- --interactive` or `rye run em -- -i`
- Install dependencies: `rye sync`
- Format code: `black .` (pre-commit hook available)
- Run tests: `pytest chapter2/test_*.py` (currently disused)
- Run single test: `pytest chapter2/test_file.py::test_function -v`
- Run all tests with asyncio support: `pytest --asyncio-mode=auto`
- Typecheck with mypy: `mypy chapter2/`
- Start embedding server (required for retrieval tasks): `rye run embed_server` (in `embed_server` directory)

## Style Guidelines
- Formatting: Black with Python 3.11 (enforced by pre-commit hook)
- Imports: Standard library first, then third-party, then local modules
- Typing: Use type hints with pydantic for data models
- Error handling: Use async/await properly, handle exceptions appropriately
- Faculty functions: Async generators taking message history ensemble, faculty config, and global em config
- Structure: Keep components decoupled and composable
- Documentation: Update documentation when adding features
- Models: Use pydantic BaseModel for structured data

## Pitfalls
- Comments and pydantic type hints may be inaccurate or describe unimplemented functionality. Code is the only true source of truth. The code is generally well-written and clear; attend to the code!
