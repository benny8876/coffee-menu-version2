"""Shared revenue and date-range helpers (income keyed on settled_at, fallback created_at)."""

from datetime import datetime, time, timezone, timedelta
from typing import Optional, Tuple, List

from sqlalchemy import func
from sqlalchemy.orm import Session

import models

MYANMAR_TZ = timezone(timedelta(hours=6, minutes=30))

# Coalesce: cash received when settled, else order placed time
INCOME_DATE = func.coalesce(models.Order.settled_at, models.Order.created_at)


def parse_target_date(date: Optional[str]):
    if date:
        try:
            return datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            raise ValueError("Invalid date format. Use YYYY-MM-DD.")
    return datetime.now(MYANMAR_TZ).date()


def day_bounds(target_date) -> Tuple[datetime, datetime]:
    return datetime.combine(target_date, time.min), datetime.combine(target_date, time.max)


def month_bounds(target_date) -> Tuple[datetime, datetime]:
    month_start = datetime(target_date.year, target_date.month, 1, 0, 0, 0)
    if target_date.month == 12:
        next_month_start = datetime(target_date.year + 1, 1, 1, 0, 0, 0)
    else:
        next_month_start = datetime(target_date.year, target_date.month + 1, 1, 0, 0, 0)
    month_end = next_month_start - timedelta(seconds=1)
    return month_start, month_end


def week_bounds(target_date) -> Tuple[datetime, datetime]:
    """Monday–Sunday week containing target_date."""
    week_start_date = target_date - timedelta(days=target_date.weekday())
    week_end_date = week_start_date + timedelta(days=6)
    return day_bounds(week_start_date)[0], day_bounds(week_end_date)[1]


def resolve_range_bounds(
    date: Optional[str] = None,
    range_type: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> Tuple[datetime, datetime, str]:
    """Return (start, end, human label) for finance/report ranges."""
    if from_date and to_date:
        try:
            start_d = datetime.strptime(from_date, "%Y-%m-%d").date()
            end_d = datetime.strptime(to_date, "%Y-%m-%d").date()
        except ValueError:
            raise ValueError("Invalid date format. Use YYYY-MM-DD.")
        if start_d > end_d:
            start_d, end_d = end_d, start_d
        label = f"{start_d.isoformat()} → {end_d.isoformat()}"
        return day_bounds(start_d)[0], day_bounds(end_d)[1], label

    target = parse_target_date(date)
    range_key = (range_type or "day").lower()

    if range_key == "week":
        start, end = week_bounds(target)
        label = f"Week of {target.isoformat()}"
    elif range_key == "month":
        start, end = month_bounds(target)
        label = target.strftime("%B %Y")
    else:
        start, end = day_bounds(target)
        label = target.isoformat()

    return start, end, label


def completed_orders_for_range(
    db: Session, start: datetime, end: datetime
) -> List[models.Order]:
    """Paid orders in range — counted by settlement date when available."""
    return (
        db.query(models.Order)
        .filter(
            models.Order.status == models.OrderStatus.COMPLETED,
            models.Order.settled_at.isnot(None),
            INCOME_DATE.between(start, end),
        )
        .order_by(INCOME_DATE.desc())
        .all()
    )
