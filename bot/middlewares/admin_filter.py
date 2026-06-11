"""Filter that allows only the configured admin TG ID."""

from __future__ import annotations

from typing import Any

from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message


class AdminFilter(BaseFilter):
    """Pass only for the single configured admin.

    Used on the admin router. Non-admin users will get a silent ignore
    (the handler will not be invoked).
    """

    def __init__(self, admin_id: int) -> None:
        self.admin_id = admin_id

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        user: Any = event.from_user
        if user is None:
            return False
        return user.id == self.admin_id
