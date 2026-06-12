"""Лимиты бесплатных генераций по типу юзера.

Business-logic: админ = безлимит, обычный юзер = 1 сайт.
"""

from __future__ import annotations

# Лимиты генераций
ADMIN_FREE_LIMIT = 999  # фактически безлимит
REGULAR_FREE_LIMIT = 1


def get_free_sites_limit(is_admin: bool) -> int:
    """Получить лимит сайтов для юзера.

    Args:
        is_admin: флаг админа

    Returns:
        Количество сайтов которые юзер может создать бесплатно
    """
    return ADMIN_FREE_LIMIT if is_admin else REGULAR_FREE_LIMIT


def can_create_site(is_admin: bool, current_sites: int) -> bool:
    """Проверить, может ли юзер создать ещё один сайт.

    Args:
        is_admin: флаг админа
        current_sites: текущее количество активных сайтов у юзера

    Returns:
        True если юзер может создать ещё один сайт
    """
    return current_sites < get_free_sites_limit(is_admin)
