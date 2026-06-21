import hashlib
import hmac
import os
from typing import Optional

from fastapi import Header, HTTPException, Query, WebSocket
from sqlalchemy.orm import Session

import models

SECRET_KEY = os.getenv(
    "RESTAURANT_SECRET_KEY", "restaurant_super_secret_signing_key_2026"
).encode()
KITCHEN_PIN = os.getenv("KITCHEN_PIN", "kitchen2026")
KITCHEN_SESSION_TOKEN = os.getenv(
    "KITCHEN_SESSION_TOKEN", "secure_kitchen_session_token_2026"
)
MANAGER_SESSION_TOKEN = os.getenv(
    "MANAGER_SESSION_TOKEN", "secure_manager_session_token_2026_xyz"
)

KITCHEN_ALLOWED_TRANSITIONS = {
    models.OrderStatus.PENDING: {
        models.OrderStatus.PREPARING,
        models.OrderStatus.CANCELLED,
    },
    models.OrderStatus.PREPARING: {
        models.OrderStatus.SERVED,
        models.OrderStatus.CANCELLED,
    },
}


def generate_table_token(table_id: int) -> str:
    return hmac.new(SECRET_KEY, str(table_id).encode(), hashlib.sha256).hexdigest()


def verify_table_token(table_id: int, token: str) -> bool:
    expected = generate_table_token(table_id)
    return hmac.compare_digest(expected, token)


def verify_kitchen_token(authorization: Optional[str] = Header(None)) -> bool:
    if not authorization:
        raise HTTPException(
            status_code=401, detail="Kitchen login required. Please enter staff PIN."
        )
    try:
        token_type, token = authorization.split(" ")
        if token_type.lower() != "bearer" or token != KITCHEN_SESSION_TOKEN:
            raise ValueError()
    except (ValueError, AttributeError):
        raise HTTPException(status_code=401, detail="Invalid kitchen session.")
    return True


def verify_kitchen_ws_token(token: Optional[str] = Query(None)) -> bool:
    if not token or token != KITCHEN_SESSION_TOKEN:
        return False
    return True


async def reject_unauthorized_kitchen_ws(websocket: WebSocket) -> None:
    await websocket.close(code=1008, reason="Kitchen authentication required")


def validate_kitchen_status_transition(
    current: models.OrderStatus, new_status: models.OrderStatus
) -> None:
    allowed = KITCHEN_ALLOWED_TRANSITIONS.get(current, set())
    if new_status not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot change order from '{current.value}' to '{new_status.value}'.",
        )


def restore_order_stock(order: models.Order, db: Session) -> None:
    for order_item in order.items:
        menu_item = order_item.menu_item
        if menu_item.stock is not None:
            menu_item.stock += order_item.quantity
            if not menu_item.is_available and menu_item.stock > 0:
                menu_item.is_available = True
