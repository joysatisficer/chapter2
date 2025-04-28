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

## Commit Guidelines
- AI-assisted commits should include a narrative describing the collaboration
- Identify AI models used (e.g., "Claude 3.7 Sonnet")
- Describe the specific interaction process between user and AI
- Include meaningful details about how ideas evolved through feedback
- Example: "User requested guidelines; Claude 3.7 Sonnet drafted several versions with iterative feedback to refine the approach"
- Chapter II is licensed under the Artistic License 2.0. Pursuant to the terms of the license, please document how the modified version differs from the standard version, including, but not limited to, documenting any non-standard features, executables, and modules.

## Pitfalls
- Comments and pydantic type hints may be inaccurate or describe unimplemented functionality. Code is the only true source of truth. The code is generally well-written and clear; attend to the code!
