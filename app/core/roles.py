"""Host principal is independent of registered admins; old owner ID is migration metadata."""
def is_owner(user_id: str | None) -> bool:
    from app.core.host_auth import HOST_ID
    return user_id == HOST_ID


def role_for(user_id: str) -> str:
    return 'owner' if is_owner(user_id) else 'admin'


def active_user(user_id: str) -> bool:
    from app.core.security import _load_users
    user = _load_users().get(user_id)
    return bool(user and not user.get('disabled'))
