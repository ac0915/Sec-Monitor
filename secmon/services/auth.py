from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import Settings
from ..database import SessionLocal, init_db
from ..models import AdminApiToken, AdminAuditEvent, AdminUser, utc_now


ROLE_LEVELS = {
    "viewer": 10,
    "operator": 20,
    "admin": 30,
}


class AuthError(RuntimeError):
    pass


@dataclass(slots=True)
class AuthPrincipal:
    user_id: int
    username: str
    role: str
    token_id: int


def role_allows(actual_role: str, required_role: str) -> bool:
    return ROLE_LEVELS.get(actual_role, 0) >= ROLE_LEVELS.get(required_role, 0)


class AuthService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def bootstrap_enabled(self) -> bool:
        return bool(self.settings.admin_bootstrap_password)

    def login(self, *, username: str, password: str, label: str | None = None) -> dict:
        init_db()
        with SessionLocal() as db:
            self._ensure_bootstrap_admin(db)
            user = db.execute(select(AdminUser).where(AdminUser.username == username)).scalar_one_or_none()
            if user is None or not user.is_active or not self._verify_password(password, user.password_hash):
                self._record_audit_event(
                    db,
                    user_id=user.id if user else None,
                    action="auth.login",
                    resource_type="admin_user",
                    resource_id=username,
                    status="denied",
                    metadata={"label": label or "api"},
                )
                db.commit()
                raise AuthError("Invalid admin username or password.")

            raw_token = secrets.token_urlsafe(32)
            expires_at = utc_now() + timedelta(seconds=self.settings.admin_token_ttl_seconds)
            token = AdminApiToken(
                user_id=user.id,
                token_hash=self._hash_token(raw_token),
                label=label or "api",
                expires_at=expires_at,
            )
            db.add(token)
            user.last_login_at = utc_now()
            db.flush()
            self._record_audit_event(
                db,
                user_id=user.id,
                action="auth.login",
                resource_type="admin_user",
                resource_id=str(user.id),
                status="success",
                metadata={"label": label or "api"},
            )
            db.commit()
            return {
                "token": raw_token,
                "expires_at": int(expires_at.timestamp()),
                "user": self._serialize_user(user),
            }

    def login_with_bootstrap_password(self, password: str, *, label: str | None = None) -> dict:
        return self.login(
            username=self.settings.admin_bootstrap_username,
            password=password,
            label=label or "gui",
        )

    def verify_access_token(self, token: str | None, *, required_role: str = "viewer") -> AuthPrincipal:
        if not token:
            raise AuthError("Missing admin access token.")

        init_db()
        with SessionLocal() as db:
            self._ensure_bootstrap_admin(db)
            token_row = db.execute(
                select(AdminApiToken, AdminUser)
                .join(AdminUser, AdminUser.id == AdminApiToken.user_id)
                .where(AdminApiToken.token_hash == self._hash_token(token))
            ).one_or_none()

            if token_row is None:
                raise AuthError("Invalid admin access token.")

            api_token, user = token_row
            now = utc_now()
            expires_at = _coerce_utc(api_token.expires_at)
            if api_token.revoked_at is not None or expires_at <= now:
                raise AuthError("Admin access token has expired.")
            if not user.is_active:
                raise AuthError("Admin user is inactive.")
            if not role_allows(user.role, required_role):
                raise AuthError("Insufficient admin permissions.")

            api_token.last_used_at = now
            db.commit()
            return AuthPrincipal(
                user_id=user.id,
                username=user.username,
                role=user.role,
                token_id=api_token.id,
            )

    def me(self, token: str | None) -> dict:
        principal = self.verify_access_token(token)
        return {
            "user": {
                "id": principal.user_id,
                "username": principal.username,
                "role": principal.role,
            }
        }

    def list_users(self, principal: AuthPrincipal) -> dict:
        self._require_role(principal, "admin")
        init_db()
        with SessionLocal() as db:
            self._ensure_bootstrap_admin(db)
            users = db.execute(select(AdminUser).order_by(AdminUser.created_at.asc())).scalars().all()
            return {"users": [self._serialize_user(user) for user in users]}

    def create_user(
        self,
        principal: AuthPrincipal,
        *,
        username: str,
        password: str,
        role: str,
    ) -> dict:
        self._require_role(principal, "admin")
        role = role.strip().lower()
        if role not in ROLE_LEVELS:
            raise AuthError("Invalid role. Use viewer, operator, or admin.")
        if not username.strip():
            raise AuthError("Username is required.")
        if len(password) < 8:
            raise AuthError("Password must be at least 8 characters.")

        init_db()
        with SessionLocal() as db:
            self._ensure_bootstrap_admin(db)
            existing = db.execute(select(AdminUser).where(AdminUser.username == username.strip())).scalar_one_or_none()
            if existing is not None:
                raise AuthError("Username already exists.")
            user = AdminUser(
                username=username.strip(),
                password_hash=self._hash_password(password),
                role=role,
            )
            db.add(user)
            db.flush()
            self._record_audit_event(
                db,
                user_id=principal.user_id,
                action="admin_user.create",
                resource_type="admin_user",
                resource_id=str(user.id),
                status="success",
                metadata={"username": user.username, "role": user.role},
            )
            db.commit()
            return {"user": self._serialize_user(user)}

    def list_audit_events(self, principal: AuthPrincipal, *, limit: int = 100) -> dict:
        self._require_role(principal, "admin")
        init_db()
        with SessionLocal() as db:
            rows = db.execute(
                select(AdminAuditEvent, AdminUser)
                .join(AdminUser, AdminUser.id == AdminAuditEvent.user_id, isouter=True)
                .order_by(AdminAuditEvent.created_at.desc())
                .limit(limit)
            ).all()
            return {
                "events": [
                    {
                        "id": event.id,
                        "action": event.action,
                        "resource_type": event.resource_type,
                        "resource_id": event.resource_id,
                        "status": event.status,
                        "metadata": event.event_metadata,
                        "created_at": event.created_at.isoformat(),
                        "user": user.username if user else None,
                    }
                    for event, user in rows
                ]
            }

    def record_admin_action(
        self,
        principal: AuthPrincipal,
        *,
        action: str,
        resource_type: str,
        resource_id: str | None = None,
        metadata: dict | None = None,
        status: str = "success",
    ) -> None:
        init_db()
        with SessionLocal() as db:
            self._record_audit_event(
                db,
                user_id=principal.user_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                status=status,
                metadata=metadata or {},
            )
            db.commit()

    def _ensure_bootstrap_admin(self, db: Session) -> None:
        users_count = db.scalar(select(func.count(AdminUser.id))) or 0
        if users_count:
            return
        if not self.bootstrap_enabled:
            return

        user = AdminUser(
            username=self.settings.admin_bootstrap_username,
            password_hash=self._hash_password(self.settings.admin_bootstrap_password or ""),
            role="admin",
        )
        db.add(user)
        db.flush()
        self._record_audit_event(
            db,
            user_id=user.id,
            action="auth.bootstrap",
            resource_type="admin_user",
            resource_id=str(user.id),
            status="success",
            metadata={"username": user.username},
        )

    def _require_role(self, principal: AuthPrincipal, role: str) -> None:
        if role_allows(principal.role, role):
            return
        raise AuthError("Insufficient admin permissions.")

    def _serialize_user(self, user: AdminUser) -> dict:
        return {
            "id": user.id,
            "username": user.username,
            "role": user.role,
            "is_active": user.is_active,
            "created_at": user.created_at.isoformat(),
            "updated_at": user.updated_at.isoformat(),
            "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        }

    def _record_audit_event(
        self,
        db: Session,
        *,
        user_id: int | None,
        action: str,
        resource_type: str,
        resource_id: str | None,
        status: str,
        metadata: dict,
    ) -> None:
        db.add(
            AdminAuditEvent(
                user_id=user_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                status=status,
                event_metadata=metadata,
            )
        )

    def _hash_token(self, token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _hash_password(self, password: str) -> str:
        salt = secrets.token_hex(16)
        iterations = 390000
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations)
        return f"pbkdf2_sha256${iterations}${salt}${digest.hex()}"

    def _verify_password(self, password: str, password_hash: str) -> bool:
        try:
            algorithm, iterations_text, salt, digest = password_hash.split("$", 3)
        except ValueError:
            return False
        if algorithm != "pbkdf2_sha256":
            return False
        check = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            int(iterations_text),
        ).hex()
        return hmac.compare_digest(check, digest)


def _coerce_utc(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc)
    return value.replace(tzinfo=timezone.utc)
