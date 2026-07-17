"""Enables `python -m app.worker` (see app/worker/main.py for the actual
entrypoint logic - this file only exists because `python -m <package>`
requires a `__main__.py`, not just a `main.py`, inside that package)."""
from app.worker.main import main

if __name__ == "__main__":
    main()
