"""Security settings."""

import os

JWT_ALGORITHM = "HS256"

# NOTE: For production, set this via env var (and rotate as needed).
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "dev-secret-change-me")

# Access token expiry is 1 day.
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24

# Login credentials for the initial JWT-only auth flow.
AUTH_USERNAME = os.getenv("AUTH_USERNAME", "admin")
AUTH_PASSWORD = os.getenv("AUTH_PASSWORD", "admin")
