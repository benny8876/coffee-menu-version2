from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session
from database import get_db
import models, schemas
from websocket import manager_ws
from typing import List 

router = APIRouter(prefix="/kitchen", tags=["Kitchen Panel"])

@router.get("/orders", response_model=List[schemas.OrderResponse])
def get_active_orders(db: Session = Depends(get_db)):
    # Cooks only view orders that have successfully processed payments (Status: Pending, Preparing, Served)
    return db.query(models.Order).filter(
        models.Order.status.in_([
            models.OrderStatus.PENDING, 
            models.OrderStatus.PREPARING, 
            models.OrderStatus.SERVED
        ])
    ).order_by(models.Order.created_at.asc()).all()

@router.patch("/orders/{order_id}/status", response_model=schemas.OrderResponse)
async def update_order_status(order_id: int, status: models.OrderStatus, db: Session = Depends(get_db)):
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    
    order.status = status
    db.commit()
    db.refresh(order)

    # Notify dashboards of transaction state alteration
    response_payload = schemas.OrderResponse.model_validate(order).model_dump(mode='json')
    await manager_ws.broadcast({"event": "status_update", "order": response_payload})

    return order

@router.websocket("/ws")
async def kitchen_websocket_endpoint(websocket: WebSocket):
    await manager_ws.connect(websocket)
    print("LOG: [WebSocket] New dashboard monitoring session accepted.")
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        print("LOG: [WebSocket] Connection closed gracefully.")
    except Exception as e:
        print(f"LOG: [WebSocket] Unexpected connection break: {e}")
    finally:
        manager_ws.disconnect(websocket)
        print("LOG: [WebSocket] Connection completely cleaned up from pool.")