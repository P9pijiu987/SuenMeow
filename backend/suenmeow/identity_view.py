"""Shared display metadata from verified forum identities, never inferred from account names."""
from sqlalchemy import select

from .database import ForumIdentity


def identity_view(s, account):
    identity = s.scalar(select(ForumIdentity).where(ForumIdentity.account_id == account.id))
    if not identity:
        return {}
    return {"display_name": identity.profile.get("name") or identity.profile["username"],
            "forum_user_id": identity.user_id, "avatar_url": "/api/auth/avatar/" + account.id}
