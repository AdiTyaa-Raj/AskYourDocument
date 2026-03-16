"""Configuration package."""

from dotenv import load_dotenv


def load_env() -> None:
    load_dotenv()


load_env()
