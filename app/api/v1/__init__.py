from fastapi import APIRouter

from app.api.v1 import auth, documents, email, health, roles, tenants, users

router = APIRouter()

router.include_router(auth.router)
router.include_router(health.router)
router.include_router(documents.router)
router.include_router(users.router)
router.include_router(roles.router)
router.include_router(tenants.router)
router.include_router(email.router)
