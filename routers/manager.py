from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, time, timezone, timedelta
from typing import Optional, List
import csv, io, hmac, hashlib

from database import get_db
import models, schemas

router = APIRouter(prefix="/manager", tags=["Manager Panel"])
SECRET_KEY = b"restaurant_super_secret_signing_key_2026"
MYANMAR_TZ = timezone(timedelta(hours=6, minutes=30))

# --- Secure Table Verification Token Generation ---
@router.get("/generate-token/{table_id}")
def generate_table_qr_token(table_id: int, db: Session = Depends(get_db)):
    table = db.query(models.RestaurantTable).filter(models.RestaurantTable.id == table_id).first()
    if not table:
        raise HTTPException(status_code=404, detail="Table does not exist.")
    
    token = hmac.new(SECRET_KEY, str(table.id).encode(), hashlib.sha256).hexdigest()
    return {
        "table_id": table.id,
        "table_number": table.number,
        "secure_token": token,
        "qr_link": f"/menu?table={table.id}&token={token}"
    }

# --- Inventory Controls ---
@router.post("/menu", response_model=schemas.MenuItemResponse)
def create_menu_item(item: schemas.MenuItemCreate, db: Session = Depends(get_db)):
    db_item = models.MenuItem(
        name=item.name,
        description=item.description,
        price=item.price,
        category=item.category,
        is_available=item.is_available,
        stock=item.stock
    )
    db.add(db_item)
    db.flush()

    for mod in item.modifiers:
        db_mod = models.MenuItemModifier(menu_item_id=db_item.id, name=mod.name, price=mod.price)
        db.add(db_mod)

    db.commit()
    db.refresh(db_item)
    return db_item

@router.put("/menu/{item_id}", response_model=schemas.MenuItemResponse)
def update_menu_item(item_id: int, updated_item: schemas.MenuItemUpdate, db: Session = Depends(get_db)):
    db_item = db.query(models.MenuItem).filter(models.MenuItem.id == item_id).first()
    if not db_item:
        raise HTTPException(status_code=404, detail="Item not found")
    
    for key, value in updated_item.model_dump(exclude_unset=True).items():
        setattr(db_item, key, value)
        
    db.commit()
    db.refresh(db_item)
    return db_item

# --- Order & Receipt Management ---
@router.get("/orders", response_model=List[schemas.OrderResponse])
def get_all_orders(db: Session = Depends(get_db)):
    return db.query(models.Order).order_by(models.Order.created_at.desc()).all()

@router.get("/orders/{order_id}/voucher")
def print_voucher(order_id: int, db: Session = Depends(get_db)):
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    voucher_data = {
        "restaurant_name": "QR Dine Inn",
        "voucher_id": f"REC-{order.id:06d}",
        "timestamp": order.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        "table_number": order.table.number,
        "items": [
            {
                "name": item.menu_item.name,
                "quantity": item.quantity,
                "unit_price": item.menu_item.price,
                "subtotal": item.quantity * item.menu_item.price
            } for item in order.items
        ],
        "subtotal": order.total_price,
        "tax_amount": round(order.total_price * 0.10, 2),
        "grand_total": round(order.total_price * 1.10, 2),
        "status": order.status.value
    }
    return voucher_data

# --- GET LIST OF LIVE TABLES (DINING BILLS IN PROGRESS) ---
@router.get("/tables/active")
def get_active_tables(db: Session = Depends(get_db)):
    active_orders = db.query(models.Order).filter(
        models.Order.status.in_([
            models.OrderStatus.PENDING,
            models.OrderStatus.PREPARING,
            models.OrderStatus.SERVED
        ])
    ).all()
    
    tables_map = {}
    for order in active_orders:
        t_id = order.table.id
        if t_id not in tables_map:
            tables_map[t_id] = {
                "table_id": t_id,
                "table_number": order.table.number,
                "active_orders_count": 0,
                "total_price": 0.0
            }
        tables_map[t_id]["active_orders_count"] += 1
        tables_map[t_id]["total_price"] += order.total_price
        
    return list(tables_map.values())

# --- GENERATE UNIFIED MASTER BILL (ACTIVE SESSIONS) ---
@router.get("/tables/{table_id}/bill")
def get_consolidated_table_bill(table_id: int, db: Session = Depends(get_db)):
    active_orders = db.query(models.Order).filter(
        models.Order.table_id == table_id,
        models.Order.status.in_([
            models.OrderStatus.PENDING,
            models.OrderStatus.PREPARING,
            models.OrderStatus.SERVED
        ])
    ).all()

    if not active_orders:
        raise HTTPException(status_code=404, detail="No active dining sessions found.")

    consolidated_items = {}
    grand_total = 0.0
    order_ids = []

    for order in active_orders:
        order_ids.append(order.id)
        for item in order.items:
            item_unit_price = item.menu_item.price
            for mod_assoc in item.selected_modifiers:
                item_unit_price += mod_assoc.modifier.price
            
            mod_key = "-".join(sorted([str(m.modifier_id) for m in item.selected_modifiers]))
            item_key = f"{item.menu_item.id}_{mod_key}"
            
            if item_key not in consolidated_items:
                consolidated_items[item_key] = {
                    "name": item.menu_item.name,
                    "quantity": 0,
                    "unit_price": item_unit_price,
                    "modifiers": [m.modifier.name for m in item.selected_modifiers]
                }
            
            consolidated_items[item_key]["quantity"] += item.quantity
            grand_total += item_unit_price * item.quantity

    table_num = active_orders[0].table.number

    return {
        "restaurant_name": "QR Dine Inn",
        "table_id": table_id,
        "table_number": table_num,
        "order_ids": order_ids,
        "timestamp": datetime.now(MYANMAR_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "items": list(consolidated_items.values()),
        "subtotal": grand_total,
        "tax_amount": round(grand_total * 0.10, 2),
        "grand_total": round(grand_total * 1.10, 2)
    }

# --- SETTLE TABLE BILL (STAMPS CLOSED TIME) ---
@router.post("/tables/{table_id}/settle")
def settle_table_bill(table_id: int, db: Session = Depends(get_db)):
    active_orders = db.query(models.Order).filter(
        models.Order.table_id == table_id,
        models.Order.status.in_([
            models.OrderStatus.PENDING,
            models.OrderStatus.PREPARING,
            models.OrderStatus.SERVED
        ])
    ).all()

    if not active_orders:
        raise HTTPException(status_code=400, detail="No active orders found to settle.")

    # Capture precise closure time in Myanmar timezone
    now_settled_time = datetime.now(MYANMAR_TZ).replace(tzinfo=None)

    for order in active_orders:
        order.status = models.OrderStatus.COMPLETED
        order.settled_at = now_settled_time # Sets shared timestamp grouping these dockets
    
    db.commit()
    return {"message": f"Table {table_id} closed."}

# --- GET TABLE BILLS HISTORY (GROUPED SESSIONS) ---
@router.get("/tables/settled-history")
def get_settled_tables_history(db: Session = Depends(get_db)):
    # Fetch all orders that have been successfully settled
    completed_orders = db.query(models.Order).filter(
        models.Order.status == models.OrderStatus.COMPLETED,
        models.Order.settled_at != None
    ).order_by(models.Order.settled_at.desc()).all()

    # Group records by table_id and unique close time
    history_map = {}
    for order in completed_orders:
        iso_time = order.settled_at.isoformat()
        key = f"{order.table_id}_{iso_time}"
        
        if key not in history_map:
            history_map[key] = {
                "table_id": order.table_id,
                "table_number": order.table.number,
                "settled_at": order.settled_at.strftime("%Y-%m-%d %H:%M:%S"),
                "settled_at_iso": iso_time,
                "order_ids": [],
                "total_price": 0.0
            }
        history_map[key]["order_ids"].append(order.id)
        history_map[key]["total_price"] += order.total_price

    return list(history_map.values())

# --- DYNAMICALLY RECONSTRUCT COMPLETED TABLE BILLS FROM HISTORY ---
@router.get("/tables/{table_id}/historical-bill")
def get_historical_table_bill(table_id: int, settled_at: str, db: Session = Depends(get_db)):
    try:
        settled_datetime = datetime.fromisoformat(settled_at)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ISO date format.")

    orders = db.query(models.Order).filter(
        models.Order.table_id == table_id,
        models.Order.status == models.OrderStatus.COMPLETED,
        models.Order.settled_at == settled_datetime
    ).all()

    if not orders:
        raise HTTPException(status_code=404, detail="No historical records found.")

    consolidated_items = {}
    grand_total = 0.0
    order_ids = []

    for order in orders:
        order_ids.append(order.id)
        for item in order.items:
            item_unit_price = item.menu_item.price
            for mod_assoc in item.selected_modifiers:
                item_unit_price += mod_assoc.modifier.price
            
            mod_key = "-".join(sorted([str(m.modifier_id) for m in item.selected_modifiers]))
            item_key = f"{item.menu_item.id}_{mod_key}"
            
            if item_key not in consolidated_items:
                consolidated_items[item_key] = {
                    "name": item.menu_item.name,
                    "quantity": 0,
                    "unit_price": item_unit_price,
                    "modifiers": [m.modifier.name for m in item.selected_modifiers]
                }
            
            consolidated_items[item_key]["quantity"] += item.quantity
            grand_total += item_unit_price * item.quantity

    table_num = orders[0].table.number

    return {
        "restaurant_name": "QR Dine Inn",
        "table_id": table_id,
        "table_number": table_num,
        "order_ids": order_ids,
        "timestamp": settled_datetime.strftime("%Y-%m-%d %H:%M:%S"),
        "items": list(consolidated_items.values()),
        "subtotal": grand_total,
        "tax_amount": round(grand_total * 0.10, 2),
        "grand_total": round(grand_total * 1.10, 2)
    }

# --- Analytics & Exporters ---
@router.get("/analytics/daily", response_model=schemas.DailyAnalytics)
def get_daily_analytics(date: Optional[str] = None, db: Session = Depends(get_db)):
    if date:
        try:
            target_date = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")
    else:
        target_date = datetime.now(MYANMAR_TZ).date()

    day_start = datetime.combine(target_date, time.min)
    day_end = datetime.combine(target_date, time.max)

    completed_orders = db.query(models.Order).filter(
        models.Order.created_at.between(day_start, day_end),
        models.Order.status == models.OrderStatus.COMPLETED
    ).all()

    total_revenue = sum(order.total_price for order in completed_orders)
    total_completed = len(completed_orders)

    popular_items = db.query(
        models.MenuItem.name,
        func.sum(models.OrderItem.quantity).label("total_sold")
    ).join(models.OrderItem, models.MenuItem.id == models.OrderItem.menu_item_id)\
     .join(models.Order, models.Order.id == models.OrderItem.order_id)\
     .filter(
         models.Order.created_at.between(day_start, day_end),
         models.Order.status == models.OrderStatus.COMPLETED
     ).group_by(models.MenuItem.name)\
     .order_by(func.sum(models.OrderItem.quantity).desc())\
     .limit(5).all()

    top_selling = [{"name": item[0], "sold_qty": item[1]} for item in popular_items]

    return schemas.DailyAnalytics(
        date=target_date.strftime("%Y-%m-%d"),
        total_revenue=total_revenue,
        total_orders_completed=total_completed,
        top_selling_items=top_selling
    )

@router.get("/analytics/export")
def export_daily_report(date: Optional[str] = None, db: Session = Depends(get_db)):
    if date:
        try:
            target_date = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")
    else:
        target_date = datetime.now(MYANMAR_TZ).date()

    day_start = datetime.combine(target_date, time.min)
    day_end = datetime.combine(target_date, time.max)

    orders = db.query(models.Order).filter(
        models.Order.created_at.between(day_start, day_end)
    ).order_by(models.Order.created_at.asc()).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Order ID", "Table Number", "Order Time", "Status", "Items Summary", "Revenue Generated ($)"])

    for order in orders:
        items_summary = "; ".join([f"{item.menu_item.name} (x{item.quantity})" for item in order.items])
        writer.writerow([
            order.id,
            order.table.number,
            order.created_at.strftime("%H:%M:%S"),
            order.status.value,
            items_summary,
            f"{order.total_price:.2f}"
        ])

    output.seek(0)
    filename = f"sales_report_{target_date.strftime('%Y-%m-%d')}.csv"
    headers = {'Content-Disposition': f'attachment; filename="{filename}"'}
    return StreamingResponse(output, media_type="text/csv", headers=headers)