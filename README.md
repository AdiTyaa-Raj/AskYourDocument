# AskYourDocument
Here you can upload your document and ask any question related to that document

## Dev server (auto-reload)

- Run with hot-reload (auto restart on code changes): `./scripts/dev.sh`
- Custom port: `PORT=8000 ./scripts/dev.sh`

## Auth (JWT)

- `POST /api/v1/login` is public and returns an `access_token` on success.
- `POST /api/v1/login` with JSON: `{"email":"adi7yaraj@gmail.com","password":"Admin@123"}` (or `{"username": "...", "password": "..."}`) returns a new `access_token` (expires in 1 day).
- Send the token on all `/api/v1/*` endpoints via header: `Authorization: Bearer <access_token>`.

Dev defaults live in `.env` (`JWT_SECRET_KEY`, `AUTH_USERNAME`, `AUTH_PASSWORD`). Change them for production.

## Seed super admin

Run: `python3.11 scripts/seed_super_admin.py` (uses the defaults requested in this repo).

Run the python file with : uvicorn main:app --reload --host 0.0.0.0 --port 8080
