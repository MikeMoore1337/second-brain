"""FastAPI entrypoint for the local Second Brain browser shell."""

from second_brain.entrypoints.web.app import create_app

__all__ = ["create_app"]
