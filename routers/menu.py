from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
import hmac
import hashlib
from typing import List 
from database import get_db
import models, schemas
from websocket import manager_ws

router = APIRouter(prefix="/menu", tags=["Menu (Client)"])

# Secret key used for cryptographic signature matching
SECRET_KEY = b"restaurant_super_secret_signing_key_2026"

def verify_table_token(table_id: int, token: str) -> bool:
    expected = hmac.new(SECRET_KEY, str(table_id).encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, token)

@router.get("/", response_model=List[schemas.MenuItemResponse])
def get_available_menu(db: Session = Depends(get_db)):
    return db.query(models.MenuItem).filter(models.MenuItem.is_available == True).all()

@router.post("/order", response_model=schemas.OrderResponse, status_code=status.HTTP_201_CREATED)
async def place_order(order_data: schemas.OrderCreate, db: Session = Depends(get_db)):
    # 1. Cryptographic Signature Validation
    if not verify_table_token(order_data.table_id, order_data.token):
        raise HTTPException(status_code=403, detail="Invalid table token. Table verification failed.")

    table = db.query(models.RestaurantTable).filter(models.RestaurantTable.id == order_data.table_id).first()
    if not table or not table.is_active:
        raise HTTPException(status_code=404, detail="Selected table is inactive or missing.")

    total_price = 0.0
    
    # Orders default to AWAITING_PAYMENT status
    db_order = models.Order(table_id=order_data.table_id, status=models.OrderStatus.AWAITING_PAYMENT)
    db.add(db_order)
    db.flush()

    for item in order_data.items:
        menu_item = db.query(models.MenuItem).filter(models.MenuItem.id == item.menu_item_id).first()
        if not menu_item or not menu_item.is_available:
            db.rollback()
            raise HTTPException(status_code=400, detail=f"Item {item.menu_item_id} is unavailable.")

        # 2. Ingredient Inventory depletion and auto-sold-out triggers
        if menu_item.stock is not None:
            if menu_item.stock < item.quantity:
                db.rollback()
                raise HTTPException(status_code=400, detail=f"Insufficient stock for {menu_item.name}. (Only {menu_item.stock} left).")
            menu_item.stock -= item.quantity
            if menu_item.stock == 0:
                menu_item.is_available = False # Set item status to sold-out automatically

        base_item_price = menu_item.price
        db_order_item = models.OrderItem(
            order_id=db_order.id,
            menu_item_id=item.menu_item_id,
            quantity=item.quantity,
            notes=item.notes
        )
        db.add(db_order_item)
        db.flush()

        # 3. Handle selection of Menu Item Modifiers
        modifier_price_accumulator = 0.0
        for mod_id in item.modifier_ids:
            modifier = db.query(models.MenuItemModifier).filter(
                models.MenuItemModifier.id == mod_id,
                models.MenuItemModifier.menu_item_id == menu_item.id
            ).first()
            if not modifier:
                db.rollback()
                raise HTTPException(status_code=400, detail=f"Selected modifier ID {mod_id} is invalid for {menu_item.name}.")
            
            modifier_price_accumulator += modifier.price
            db_item_mod = models.OrderItemModifier(
                order_item_id=db_order_item.id,
                modifier_id=modifier.id
            )
            db.add(db_item_mod)

        # Compound calculation of Base + Modifiers per unit quantity
        total_price += (base_item_price + modifier_price_accumulator) * item.quantity

    db_order.total_price = total_price
    db.commit()
    db.refresh(db_order)
    return db_order

# 4. Instant Mock Payment Route (Transitions state to Paid -> Kitchen Notification)
@router.post("/order/{order_id}/mock-pay", response_model=schemas.OrderResponse)
async def process_mock_payment(order_id: int, db: Session = Depends(get_db)):
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Target order does not exist.")
    if order.status != models.OrderStatus.AWAITING_PAYMENT:
        raise HTTPException(status_code=400, detail="Order has already been processed/paid.")

    # Mark as Paid & send to active Kitchen processing queue
    order.status = models.OrderStatus.PENDING
    db.commit()
    db.refresh(order)

    # Convert model to validated JSON format before dispatching
    response_payload = schemas.OrderResponse.model_validate(order).model_dump(mode='json')
    await manager_ws.broadcast({"event": "new_order", "order": response_payload})

    return order

# 5. Service Assistant Alert route (Waiter Summoning via WebSocket)
@router.post("/call-waiter")
async def call_waiter(table_id: int, request_type: str, db: Session = Depends(get_db)):
    table = db.query(models.RestaurantTable).filter(models.RestaurantTable.id == table_id).first()
    if not table:
        raise HTTPException(status_code=404, detail="Table not found.")
    
    await manager_ws.broadcast({
        "event": "service_request",
        "table_number": table.number,
        "request": request_type
    })
    return {"message": "Service alert successfully dispatched."}