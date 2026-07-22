"""Shared order creation logic for customer menu and manager counter sales."""

from typing import List

from fastapi import HTTPException
from sqlalchemy.orm import Session

import models
from schemas import OrderItemCreate


def create_order_from_items(
    db: Session,
    table_id: int,
    items: List[OrderItemCreate],
    initial_status: models.OrderStatus = models.OrderStatus.AWAITING_PAYMENT,
) -> models.Order:
    if not items:
        raise HTTPException(status_code=400, detail="Order must include at least one item.")

    total_price = 0.0
    db_order = models.Order(table_id=table_id, status=initial_status)
    db.add(db_order)
    db.flush()

    for item in items:
        menu_item = (
            db.query(models.MenuItem)
            .filter(models.MenuItem.id == item.menu_item_id)
            .first()
        )
        if not menu_item or not menu_item.is_available:
            db.rollback()
            raise HTTPException(
                status_code=400,
                detail=f"Item {item.menu_item_id} is unavailable.",
            )

        if menu_item.stock is not None:
            if menu_item.stock < item.quantity:
                db.rollback()
                raise HTTPException(
                    status_code=400,
                    detail=f"Insufficient stock for {menu_item.name}. (Only {menu_item.stock} left).",
                )
            menu_item.stock -= item.quantity
            if menu_item.stock == 0:
                menu_item.is_available = False

        base_item_price = menu_item.price
        db_order_item = models.OrderItem(
            order_id=db_order.id,
            menu_item_id=item.menu_item_id,
            quantity=item.quantity,
            notes=item.notes,
        )
        db.add(db_order_item)
        db.flush()

        modifier_price_accumulator = 0.0
        for mod_id in item.modifier_ids or []:
            modifier = (
                db.query(models.MenuItemModifier)
                .filter(
                    models.MenuItemModifier.id == mod_id,
                    models.MenuItemModifier.menu_item_id == menu_item.id,
                )
                .first()
            )
            if not modifier:
                db.rollback()
                raise HTTPException(
                    status_code=400,
                    detail=f"Selected modifier ID {mod_id} is invalid for {menu_item.name}.",
                )

            modifier_price_accumulator += modifier.price
            db_item_mod = models.OrderItemModifier(
                order_item_id=db_order_item.id, modifier_id=modifier.id
            )
            db.add(db_item_mod)

        total_price += (base_item_price + modifier_price_accumulator) * item.quantity

    db_order.total_price = total_price
    db.flush()
    return db_order
