"""Buildo 3-level referral system.

Levels:
  L1 (direct referrer):  30% of any payment by referred user
  L2 (referrer's referrer): 10%
  L3 (3rd level up):       5%

Signup events: just record who-referred-whom. No commission.
Payment events: distribute commissions up the chain (max 3 levels).

Privacy rule:
  - Referrer sees ONLY an anonymous message ("Пользователь присоединился
    по вашей ссылке", "Оплата 1500₽ от пользователя по вашей ссылке L1")
  - Admin gets FULL info (tg_user_id, username, link, amount, level)
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from bot.services import database

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Workaround: psycopg rows come back as dict-like (row_factory=dict_row) but
# pyright sees TupleRow. Use _Row alias for type hints.
_Row = dict[str, Any]

# Commission percentages by level
COMMISSION_PCT = {1: 0.30, 2: 0.10, 3: 0.05}


@dataclass
class ReferralLink:
    """Result of get_or_create_referral_link."""

    code: str
    bot_url: str
    total_referrals: int
    total_earnings_rub: float
    by_level: dict[int, int]  # level -> count of referrals


def _generate_code() -> str:
    """Short URL-safe code: 8 chars, base32."""
    return secrets.token_urlsafe(6)[:8].upper()


async def get_or_create_referral_code(tg_user_id: int) -> str | None:
    """Get user's referral code (creates one if doesn't exist)."""
    pool = await database.get_pool()
    if pool is None:
        return None
    try:
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                # Check existing
                await cur.execute(
                    "SELECT id, referral_code FROM users WHERE tg_user_id = %s",
                    (tg_user_id,),
                )
                row = cast(_Row, await cur.fetchone())
                if not row:
                    return None
                if row.get("referral_code"):
                    return row["referral_code"]
                # Create new
                for _ in range(5):  # retry on collision
                    new_code = _generate_code()
                    try:
                        await cur.execute(
                            "UPDATE users SET referral_code = %s WHERE id = %s RETURNING referral_code",
                            (new_code, row["id"]),
                        )
                        updated = cast(_Row, await cur.fetchone())
                        if updated:
                            return updated["referral_code"]
                    except Exception:  # noqa: BLE001
                        await conn.rollback()
                        continue
                return None
    except Exception:  # noqa: BLE001
        logger.exception("get_or_create_referral_code failed")
        return None


def make_bot_link(code: str, bot_username: str = "buildo_aibot") -> str:
    """Build a t.me deep link with the referral code as start parameter."""
    return f"https://t.me/{bot_username}?start=ref_{code}"


@dataclass
class ReferrerInfo:
    """User record for admin notification."""

    id: int
    tg_user_id: int | None
    tg_username: str | None
    tg_first_name: str | None
    level: int


async def record_signup(
    new_user_tg_id: int,
    new_user_username: str | None,
    new_user_first_name: str | None,
    ref_code: str | None,
) -> list[ReferrerInfo]:
    """Record a new user signup with referral attribution.

    Walks up the referral chain (max 3 levels), records signup events
    for each level's referrer. Returns referrer info for admin notification.
    """
    if not ref_code:
        return []
    pool = await database.get_pool()
    if pool is None:
        return []
    try:
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                # 1) Find referrer by code
                await cur.execute(
                    "SELECT id, referred_by_user_id FROM users WHERE referral_code = %s",
                    (ref_code,),
                )
                ref_row = cast(_Row, await cur.fetchone())
                if not ref_row:
                    logger.info("signup ref_code=%s not found", ref_code)
                    return []
                l1_referrer_id: int = ref_row["id"]
                l2_referrer_id: int | None = ref_row.get("referred_by_user_id")
                l3_referrer_id: int | None = None
                if l2_referrer_id:
                    await cur.execute(
                        "SELECT referred_by_user_id FROM users WHERE id = %s",
                        (l2_referrer_id,),
                    )
                    l2_row = cast(_Row, await cur.fetchone())
                    if l2_row:
                        l3_referrer_id = l2_row.get("referred_by_user_id")

                # 2) Get/upsert the new user
                await cur.execute(
                    """
                    INSERT INTO users (tg_user_id, tg_username, tg_first_name, referred_by_user_id)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (tg_user_id) DO UPDATE
                      SET referred_by_user_id = COALESCE(users.referred_by_user_id, EXCLUDED.referred_by_user_id),
                          tg_username = COALESCE(EXCLUDED.tg_username, users.tg_username),
                          tg_first_name = COALESCE(EXCLUDED.tg_first_name, users.tg_first_name),
                          updated_at = now()
                    RETURNING id
                    """,
                    (
                        new_user_tg_id,
                        new_user_username,
                        new_user_first_name,
                        l1_referrer_id,
                    ),
                )
                new_row = cast(_Row, await cur.fetchone())
                if not new_row:
                    return []
                new_user_id: int = new_row["id"]

                # 3) Record signup events at each level
                chain = [
                    (1, l1_referrer_id),
                    (2, l2_referrer_id),
                    (3, l3_referrer_id),
                ]
                referrers: list[ReferrerInfo] = []
                for level, referrer_id in chain:
                    if referrer_id is None:
                        continue
                    await cur.execute(
                        """
                        INSERT INTO referral_events
                            (event_type, source_user_id, referrer_user_id, level, commission_rub)
                        VALUES ('signup', %s, %s, %s, 0)
                        """,
                        (new_user_id, referrer_id, level),
                    )
                    # Fetch referrer tg info for admin
                    await cur.execute(
                        "SELECT id, tg_user_id, tg_username, tg_first_name FROM users WHERE id = %s",
                        (referrer_id,),
                    )
                    r = cast(_Row, await cur.fetchone())
                    if r:
                        referrers.append(
                            ReferrerInfo(
                                id=r["id"],
                                tg_user_id=r.get("tg_user_id"),
                                tg_username=r.get("tg_username"),
                                tg_first_name=r.get("tg_first_name"),
                                level=level,
                            )
                        )
                return referrers
    except Exception:  # noqa: BLE001
        logger.exception("record_signup failed")
        return []


async def record_payment(
    paying_user_id: int,
    payment_id: str,
    amount_rub: float,
) -> list[tuple[ReferrerInfo, float]]:
    """Record a payment and distribute commissions up the chain (3 levels).

    Returns list of (referrer, commission) for admin notification.
    """
    pool = await database.get_pool()
    if pool is None:
        return []
    try:
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                # Walk chain
                await cur.execute(
                    "SELECT referred_by_user_id FROM users WHERE id = %s",
                    (paying_user_id,),
                )
                row = cast(_Row, await cur.fetchone())
                if not row or not row.get("referred_by_user_id"):
                    return []
                l1 = row["referred_by_user_id"]
                chain_ids: list[tuple[int, int]] = []  # (level, user_id)
                if l1:
                    chain_ids.append((1, l1))
                    await cur.execute(
                        "SELECT referred_by_user_id FROM users WHERE id = %s", (l1,)
                    )
                    r1 = cast(_Row, await cur.fetchone())
                    l2 = r1.get("referred_by_user_id") if r1 else None
                    if l2:
                        chain_ids.append((2, l2))
                        await cur.execute(
                            "SELECT referred_by_user_id FROM users WHERE id = %s", (l2,)
                        )
                        r2 = cast(_Row, await cur.fetchone())
                        l3 = r2.get("referred_by_user_id") if r2 else None
                        if l3:
                            chain_ids.append((3, l3))

                # Distribute commissions
                results: list[tuple[ReferrerInfo, float]] = []
                for level, referrer_id in chain_ids:
                    pct = COMMISSION_PCT.get(level, 0)
                    commission = round(amount_rub * pct, 2)
                    if commission <= 0:
                        continue
                    # Record event
                    await cur.execute(
                        """
                        INSERT INTO referral_events
                            (event_type, source_user_id, referrer_user_id, level,
                             payment_id, commission_rub)
                        VALUES ('payment', %s, %s, %s, %s, %s)
                        """,
                        (paying_user_id, referrer_id, level, payment_id, commission),
                    )
                    # Credit balance
                    await cur.execute(
                        """
                        UPDATE users
                        SET referral_balance_rub = referral_balance_rub + %s
                        WHERE id = %s
                        """,
                        (commission, referrer_id),
                    )
                    # Get referrer info
                    await cur.execute(
                        "SELECT id, tg_user_id, tg_username, tg_first_name FROM users WHERE id = %s",
                        (referrer_id,),
                    )
                    r = cast(_Row, await cur.fetchone())
                    if r:
                        results.append(
                            (
                                ReferrerInfo(
                                    id=r["id"],
                                    tg_user_id=r.get("tg_user_id"),
                                    tg_username=r.get("tg_username"),
                                    tg_first_name=r.get("tg_first_name"),
                                    level=level,
                                ),
                                commission,
                            )
                        )
                return results
    except Exception:  # noqa: BLE001
        logger.exception("record_payment failed")
        return []


async def get_referral_stats(tg_user_id: int) -> ReferralLink | None:
    """Get user's referral link + stats for /referral command."""
    code = await get_or_create_referral_code(tg_user_id)
    if not code:
        return None
    pool = await database.get_pool()
    if pool is None:
        return ReferralLink(
            code=code,
            bot_url=make_bot_link(code),
            total_referrals=0,
            total_earnings_rub=0.0,
            by_level={1: 0, 2: 0, 3: 0},
        )
    try:
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                # Get user id
                await cur.execute(
                    "SELECT id FROM users WHERE tg_user_id = %s", (tg_user_id,)
                )
                row = cast(_Row, await cur.fetchone())
                if not row:
                    return None
                user_id: int = row["id"]
                # Get total earnings
                await cur.execute(
                    "SELECT COALESCE(referral_balance_rub, 0) AS bal FROM users WHERE id = %s",
                    (user_id,),
                )
                bal_row = cast(_Row, await cur.fetchone())
                total_earnings: float = float(bal_row["bal"]) if bal_row else 0.0
                # Get referral count by level
                await cur.execute(
                    """
                    SELECT level, COUNT(DISTINCT source_user_id) AS cnt
                    FROM referral_events
                    WHERE referrer_user_id = %s AND event_type = 'signup'
                    GROUP BY level
                    """,
                    (user_id,),
                )
                by_level: dict[int, int] = {1: 0, 2: 0, 3: 0}
                for r in cast(list[_Row], await cur.fetchall()):
                    by_level[r["level"]] = r["cnt"]
                total = sum(by_level.values())
                return ReferralLink(
                    code=code,
                    bot_url=make_bot_link(code),
                    total_referrals=total,
                    total_earnings_rub=total_earnings,
                    by_level=by_level,
                )
    except Exception:  # noqa: BLE001
        logger.exception("get_referral_stats failed")
        return None
