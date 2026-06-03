"""JIT user provisioning for OIDC SSO (Epic #26 P3.3).

Maps an IdP's ``userinfo`` claims onto PFactory's User +
OrganizationMember model. Stable lookup is by ``sub`` (subject
identifier), which is the IdP's permanent ID for the user — falling
back to email only when sub is unavailable from the IdP.

Role mapping: env var ``APP_OIDC_GROUP_TO_ROLE`` is a JSON object
mapping IdP group names to internal roles. First match wins. If no
group in the user's ``groups`` claim is in the map, the default role
from ``APP_OIDC_DEFAULT_ROLE`` (or "member") is used.

Default organization: env var ``APP_OIDC_DEFAULT_ORG_SLUG`` (default
"default") names the Organization that JIT-provisioned users join.
If the org doesn't exist, it's created with the first JIT user as
owner — useful for fresh installs.
"""

from __future__ import annotations

import json
import logging
import os
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import Organization, OrgMember, User

logger = logging.getLogger(__name__)


def _slugify(text: str) -> str:
    """Mirror the slug helper from auth_routes.py."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text or "").strip("-").lower()
    return slug or "org"


def _role_for_userinfo(userinfo: dict) -> str:
    """Resolve the internal role from the IdP's userinfo claims."""
    default_role = os.environ.get("APP_OIDC_DEFAULT_ROLE", "member")
    mapping_json = os.environ.get("APP_OIDC_GROUP_TO_ROLE", "")
    if not mapping_json:
        return default_role
    try:
        mapping: dict[str, str] = json.loads(mapping_json)
    except json.JSONDecodeError:
        logger.warning(
            "APP_OIDC_GROUP_TO_ROLE is not valid JSON; falling back to default role"
        )
        return default_role
    groups = userinfo.get("groups") or []
    if isinstance(groups, str):
        groups = [groups]
    for group in groups:
        if group in mapping:
            return mapping[group]
    return default_role


async def _get_or_create_default_org(
    db: AsyncSession, owner: User
) -> Organization:
    """Return the Organization JIT users join. Creates it on first call."""
    slug = os.environ.get("APP_OIDC_DEFAULT_ORG_SLUG", "default")
    result = await db.execute(select(Organization).where(Organization.slug == slug))
    org = result.scalar_one_or_none()
    if org is not None:
        return org
    org = Organization(
        name=os.environ.get("APP_OIDC_DEFAULT_ORG_NAME", "Default Organization"),
        slug=slug,
        owner_id=owner.id,
        plan="free",
    )
    db.add(org)
    await db.commit()
    await db.refresh(org)
    logger.info("Auto-created default Organization slug=%s for OIDC users", slug)
    return org


async def jit_provision_user(
    db: AsyncSession,
    userinfo: dict,
) -> User:
    """Find-or-create User + OrganizationMember for an OIDC userinfo dict.

    Lookup precedence:
      1. ``users.oidc_sub == userinfo['sub']``  (stable IdP identifier)
      2. ``users.email == userinfo['email']``   (one-time bind for users
                                                  who registered locally
                                                  before SSO was enabled)
      3. Create a new User.

    Always ensures an OrganizationMember row exists in the configured
    default org with the claim-mapped role. Updates the role on every
    login so IdP-side group changes propagate immediately.
    """
    sub = userinfo["sub"]
    email = userinfo["email"]
    name = userinfo.get("name") or email.split("@")[0]
    role = _role_for_userinfo(userinfo)

    # Lookup by sub first (stable).
    result = await db.execute(select(User).where(User.oidc_sub == sub))
    user = result.scalar_one_or_none()

    if user is None:
        # Fall back to email lookup so a user who pre-registered locally
        # and now signs in via OIDC doesn't double-create.
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        if user is not None:
            user.oidc_sub = sub  # bind for future logins
            logger.info(
                "OIDC bound existing local user %s to sub=%s", email, sub
            )

    if user is None:
        # Genuine new user — JIT-provision.
        user = User(
            email=email,
            name=name,
            password_hash="",  # OIDC users have no local password
            role=role,
            is_active=True,
            oidc_sub=sub,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        logger.info(
            "OIDC JIT-provisioned new user: %s (sub=%s role=%s)",
            email,
            sub,
            role,
        )

    # Ensure OrganizationMember row exists with the current role.
    org = await _get_or_create_default_org(db, user)
    result = await db.execute(
        select(OrgMember).where(
            OrgMember.org_id == org.id, OrgMember.user_id == user.id
        )
    )
    membership = result.scalar_one_or_none()
    if membership is None:
        membership = OrgMember(org_id=org.id, user_id=user.id, role=role)
        db.add(membership)
        await db.commit()
        logger.info(
            "OIDC added user %s to org %s with role %s", email, org.slug, role
        )
    elif membership.role != role:
        # IdP-side role changed; sync.
        membership.role = role
        await db.commit()
        logger.info(
            "OIDC updated role for user %s in org %s: %s",
            email,
            org.slug,
            role,
        )

    return user
