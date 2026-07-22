from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session
from database import get_db
import models, schemas
from websocket import manager_ws
from typing import List
from table_labels import is_counter_table
import security

router = APIRouter(prefix="/kitchen", tags=["Kitchen Panel"])


@router.post("/login")
def kitchen_login(credentials: schemas.KitchenLoginRequest):
    if credentials.pin != security.KITCHEN_PIN:
        raise HTTPException(status_code=401, detail="Invalid kitchen PIN.")
    return {"token": security.KITCHEN_SESSION_TOKEN}


@router.get("/orders", response_model=List[schemas.OrderResponse])
def get_active_orders(
    db: Session = Depends(get_db),
    authenticated: bool = Depends(security.verify_kitchen_token),
):
    return (
        db.query(models.Order)
        .filter(
            models.Order.status.in_(
                [
                    models.OrderStatus.PENDING,
                    models.OrderStatus.PREPARING,
                    models.OrderStatus.SERVED,
                ]
            )
        )
        .order_by(models.Order.created_at.asc())
        .all()
    )


@router.patch("/orders/{order_id}/status", response_model=schemas.OrderResponse)
async def update_order_status(
    order_id: int,
    status: models.OrderStatus,
    db: Session = Depends(get_db),
    authenticated: bool = Depends(security.verify_kitchen_token),
):
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    security.validate_kitchen_status_transition(order.status, status)

    if status == models.OrderStatus.CANCELLED:
        security.restore_order_stock(order, db)

    order.status = status
    if status == models.OrderStatus.SERVED and is_counter_table(order.table):
        order.status = models.OrderStatus.COMPLETED

    db.commit()
    db.refresh(order)

    response_payload = schemas.OrderResponse.model_validate(order).model_dump(mode="json")
    await manager_ws.broadcast({"event": "status_update", "order": response_payload})

    return order


@router.post("/orders/{order_id}/cancel", response_model=schemas.OrderResponse)
async def cancel_order(
    order_id: int,
    db: Session = Depends(get_db),
    authenticated: bool = Depends(security.verify_kitchen_token),
):
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    security.validate_kitchen_status_transition(order.status, models.OrderStatus.CANCELLED)
    security.restore_order_stock(order, db)
    order.status = models.OrderStatus.CANCELLED
    db.commit()
    db.refresh(order)

    response_payload = schemas.OrderResponse.model_validate(order).model_dump(mode="json")
    await manager_ws.broadcast({"event": "status_update", "order": response_payload})

    return order


@router.websocket("/ws")
async def kitchen_websocket_endpoint(websocket: WebSocket):
    token = websocket.query_params.get("token")
    if not security.verify_kitchen_ws_token(token):
        await security.reject_unauthorized_kitchen_ws(websocket)
        return

    await manager_ws.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"LOG: [WebSocket] Unexpected connection break: {e}")
    finally:
        manager_ws.disconnect(websocket)
