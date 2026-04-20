"""Configuration package."""

from pathlib import Path

from dotenv import load_dotenv


def load_env() -> None:
    # Make .env loading independent of the working directory.
    backend_root = Path(__file__).resolve().parents[2]
    env_path = backend_root / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)
    else:
        load_dotenv()


load_env()
