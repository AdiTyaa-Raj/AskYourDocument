from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.orm import Session

from app.config import load_env
from app.config.db import bootstrap_schema, get_session_factory, is_db_configured
from app.models.access_control import Permission, Role, RolePermission, Tenant, User, UserRole
from app.utils.passwords import hash_password


def main() -> int:
    load_env()

    parser = argparse.ArgumentParser(description="Seed a super-admin user across all tenants.")
    parser.add_argument("--email", default="adi7yaraj@gmail.com")
    parser.add_argument("--password", default="Admin@123")
    parser.add_argument("--name", default="Aditya Raj")
    args = parser.parse_args()

    if not is_db_configured():
        print("Database is not configured. Set DATABASE_URL or POSTGRES_* env vars.", file=sys.stderr)
        return 1

    bootstrap_schema()
    session_factory = get_session_factory()
    with session_factory() as db:
        seed_super_admin(db, email=args.email, password=args.password, name=args.name)
        db.commit()
    print("Seeded super admin successfully.")
    return 0


def seed_super_admin(db: Session, *, email: str, password: str, name: str) -> None:
    def ensure_role(*, tenant_id: int, role_name: str, description: str) -> None:
        role = (
            db.query(Role)
            .filter(Role.tenant_id == tenant_id, Role.name == role_name)
            .one_or_none()
        )
        if role is None:
            db.add(
                Role(
                    tenant_id=tenant_id,
                    name=role_name,
                    description=description,
                    is_active=True,
                )
            )

    tenants = db.query(Tenant).all()
    if not tenants:
        default_tenant = Tenant(
            name="Default Tenant",
            slug="default",
            superuser_name=name,
            superuser_email=email,
            is_active=True,
        )
        db.add(default_tenant)
        db.flush()
        tenants = [default_tenant]

    permission = (
        db.query(Permission).filter(Permission.resource == "*", Permission.action == "*").one_or_none()
    )
    if permission is None:
        permission = Permission(resource="*", action="*", description="Super admin (all access)")
        db.add(permission)
        db.flush()

    for tenant in tenants:
        if tenant.superuser_email != email or tenant.superuser_name != name:
            tenant.superuser_email = email
            tenant.superuser_name = name

        ensure_role(
            tenant_id=tenant.id,
            role_name="tenant_admin",
            description="Tenant admin (seeded)",
        )
        ensure_role(
            tenant_id=tenant.id,
            role_name="tenant_member",
            description="Tenant member (seeded)",
        )
        db.flush()

        role = (
            db.query(Role)
            .filter(Role.tenant_id == tenant.id, Role.name == "super_admin")
            .one_or_none()
        )
        if role is None:
            role = Role(
                tenant_id=tenant.id,
                name="super_admin",
                description="Tenant super admin (seeded)",
                is_active=True,
            )
            db.add(role)
            db.flush()

        role_permission = (
            db.query(RolePermission)
            .filter(RolePermission.role_id == role.id, RolePermission.permission_id == permission.id)
            .one_or_none()
        )
        if role_permission is None:
            db.add(RolePermission(role_id=role.id, permission_id=permission.id))

        user = (
            db.query(User)
            .filter(User.tenant_id == tenant.id, User.email == email)
            .one_or_none()
        )
        if user is None:
            user = User(
                tenant_id=tenant.id,
                email=email,
                full_name=name,
                password_hash=hash_password(password),
                is_active=True,
            )
            db.add(user)
            db.flush()
        else:
            user.full_name = name
            user.password_hash = hash_password(password)
            user.is_active = True

        user_role = (
            db.query(UserRole).filter(UserRole.user_id == user.id, UserRole.role_id == role.id).one_or_none()
        )
        if user_role is None:
            db.add(UserRole(user_id=user.id, role_id=role.id))


if __name__ == "__main__":
    raise SystemExit(main())
