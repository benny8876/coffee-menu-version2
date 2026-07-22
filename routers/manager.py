from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Header, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, time, timezone, timedelta
from typing import Optional, List
import hashlib, shutil, uuid
import os
from database import get_db
import models, schemas
import security
from websocket import manager_ws
from table_labels import RESTAURANT_NAME, get_table_label, COUNTER_TABLE_NUMBER, is_counter_table
from services.orders import create_order_from_items
from sqlalchemy.orm import joinedload

router = APIRouter(prefix="/manager", tags=["Manager Panel"])
MYANMAR_TZ = timezone(timedelta(hours=6, minutes=30))

# Hashing utility
def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


# Security Interceptor
def verify_manager_token(authorization: Optional[str] = Header(None)):
    if not authorization:
        raise HTTPException(
            status_code=401, detail="Missing Authorization Header. Please login."
        )
    try:
        token_type, token = authorization.split(" ")
        if token_type.lower() != "bearer" or token != security.MANAGER_SESSION_TOKEN:
            raise ValueError()
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=401, detail="Unauthorized session. Please log in."
        )
    return True


# --- Updated: Secure Database Login (No longer hardcoded) ---
@router.post("/login")
def manager_login(credentials: schemas.LoginRequest, db: Session = Depends(get_db)):
    if credentials.username != security.MANAGER_USERNAME:
        raise HTTPException(status_code=401, detail="Invalid username or password.")

    admin = (
        db.query(models.AdminCredential)
        .filter(models.AdminCredential.username == security.MANAGER_USERNAME)
        .first()
    )
    if not admin or admin.password_hash != hash_password(credentials.password):
        raise HTTPException(status_code=401, detail="Invalid username or password.")

    return {"token": security.MANAGER_SESSION_TOKEN}


# --- NEW: Change Password API Endpoint ---
@router.post("/change-password")
def change_password(
    data: schemas.PasswordChangeRequest,
    db: Session = Depends(get_db),
    authenticated: bool = Depends(verify_manager_token),
):
    # Retrieve the admin record
    admin = (
        db.query(models.AdminCredential)
        .filter(models.AdminCredential.username == security.MANAGER_USERNAME)
        .first()
    )
    if not admin:
        raise HTTPException(status_code=404, detail="Admin configuration missing.")

    # 1. Verify old password matches current database hash
    if admin.password_hash != hash_password(data.old_password):
        raise HTTPException(status_code=400, detail="Incorrect old password.")

    # 2. Write new hashed password to DB
    admin.password_hash = hash_password(data.new_password)
    db.commit()

    return {"message": "Password changed successfully. Please log in again."}


# Create static directories if they don't exist on system startup
UPLOAD_DIR = "static/uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)


# --- IMAGE UPLOADS ---
@router.post("/upload-image")
def upload_menu_image(
    file: UploadFile = File(...), authenticated: bool = Depends(verify_manager_token)
):
    file_ext = os.path.splitext(file.filename)[1].lower()
    if file_ext not in [".jpg", ".jpeg", ".png", ".webp"]:
        raise HTTPException(status_code=400, detail="Unsupported file format.")

    unique_filename = f"{uuid.uuid4()}{file_ext}"
    destination_path = os.path.join(UPLOAD_DIR, unique_filename)

    with open(destination_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    return {"image_url": f"/static/uploads/{unique_filename}"}


# --- SECURE TABLE QR GENERATOR ---
@router.get("/generate-token/{table_id}")
def generate_table_qr_token(
    table_id: int,
    db: Session = Depends(get_db),
    authenticated: bool = Depends(verify_manager_token),
):
    table = (
        db.query(models.RestaurantTable)
        .filter(models.RestaurantTable.id == table_id)
        .first()
    )
    if not table:
        raise HTTPException(status_code=404, detail="Table does not exist.")

    token = security.generate_table_token(table.id)
    return {
        "table_id": table.id,
        "table_number": get_table_label(table),
        "secure_token": token,
        "qr_link": f"/menu?table={table.id}&token={token}",
    }


@router.get("/tables/qr-links")
def get_all_table_qr_links(
    db: Session = Depends(get_db),
    authenticated: bool = Depends(verify_manager_token),
):
    tables = (
        db.query(models.RestaurantTable)
        .filter(
            models.RestaurantTable.is_active == True,
            models.RestaurantTable.number != COUNTER_TABLE_NUMBER,
        )
        .order_by(models.RestaurantTable.number.asc())
        .all()
    )
    return [
        {
            "table_id": table.id,
            "table_number": get_table_label(table),
            "secure_token": security.generate_table_token(table.id),
            "qr_link": f"/menu?table={table.id}&token={security.generate_table_token(table.id)}",
        }
        for table in tables
    ]


# --- INVENTORY CREATION ---
@router.post("/menu", response_model=schemas.MenuItemResponse)
def create_menu_item(
    item: schemas.MenuItemCreate,
    db: Session = Depends(get_db),
    authenticated: bool = Depends(verify_manager_token),
):
    # ၁။ လက်ရှိ Database ထဲမှာ အကြီးဆုံး order_index ကို ရှာမယ်
    # အကယ်၍ Item တစ်ခုမှ မရှိသေးရင် 0 ကို ယူမယ်
    max_index = db.query(func.max(models.MenuItem.order_index)).scalar() or 0
    
    # ၂။ အသစ်ထည့်မယ့် Item ရဲ့ order_index ကို max_index + 1 လို့ သတ်မှတ်လိုက်မယ်
    db_item = models.MenuItem(
        name=item.name,
        description=item.description,
        price=item.price,
        category=item.category,
        is_available=item.is_available,
        stock=item.stock,
        image_url=item.image_url,
        order_index=max_index + 1  # 🔥 ဒီနေရာလေးပဲ အဓိက ပြင်ရတာပါ
    )
    db.add(db_item)
    db.flush() # ID ရဖို့အတွက် flush ခံမယ်

    for mod in item.modifiers:
        db_mod = models.MenuItemModifier(
            menu_item_id=db_item.id, name=mod.name, price=mod.price
        )
        db.add(db_mod)

    db.commit()
    db.refresh(db_item)
    return db_item


# --- INVENTORY UPDATES ---
@router.put("/menu/{item_id}", response_model=schemas.MenuItemResponse)
def update_menu_item(
    item_id: int,
    updated_item: schemas.MenuItemUpdate,
    db: Session = Depends(get_db),
    authenticated: bool = Depends(verify_manager_token),
):
    db_item = db.query(models.MenuItem).filter(models.MenuItem.id == item_id).first()
    if not db_item:
        raise HTTPException(status_code=404, detail="Item not found")

    for key, value in updated_item.model_dump(exclude_unset=True).items():
        setattr(db_item, key, value)

    db.commit()
    db.refresh(db_item)
    return db_item

# --- Updated: Category-Aware & Sequential Index Swapping ---
@router.post("/menu/items/{item_id}/move")
async def move_menu_item(
    item_id: int, 
    direction: str, 
    db: Session = Depends(get_db),
    authenticated: bool = Depends(verify_manager_token)
):
    # 1. Locate the current item
    current_item = db.query(models.MenuItem).filter(models.MenuItem.id == item_id).first()
    if not current_item:
        raise HTTPException(status_code=404, detail="Item not found")

    # 2. Fetch all items in the SAME category, sorted by their index and database ID
    items_in_category = db.query(models.MenuItem).filter(
        models.MenuItem.category == current_item.category
    ).order_by(models.MenuItem.order_index.asc(), models.MenuItem.id.asc()).all()

    # 3. Automatically assign sequential order_indices to fix default 0 values and duplicates
    for idx, item in enumerate(items_in_category):
        item.order_index = idx
    db.flush()

    # 4. Find the position of the current item in the sequential list
    current_idx = items_in_category.index(current_item)

    # 5. Determine the target index to swap with based on direction
    if direction == "up" and current_idx > 0:
        target_idx = current_idx - 1
    elif direction == "down" and current_idx < len(items_in_category) - 1:
        target_idx = current_idx + 1
    else:
        # Prevent moving up past the top item or down past the bottom item
        return {"status": "no_change"}

    # 6. Swap indices of current and target items
    target_item = items_in_category[target_idx]
    current_item.order_index, target_item.order_index = target_item.order_index, current_item.order_index

    db.commit()
    return {"status": "success"}


# --- ALL ORDERS LOG ---
@router.get("/orders", response_model=List[schemas.OrderResponse])
def get_all_orders(
    db: Session = Depends(get_db), authenticated: bool = Depends(verify_manager_token)
):
    return db.query(models.Order).order_by(models.Order.created_at.desc()).all()


# --- PRINT VOUCHER DOCKET ---
@router.get("/orders/{order_id}/voucher")
def print_voucher(
    order_id: int,
    db: Session = Depends(get_db),
    authenticated: bool = Depends(verify_manager_token),
):
    order = db.query(models.Order).filter(models.Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    voucher_data = {
        "restaurant_name": RESTAURANT_NAME,
        "voucher_id": f"REC-{order.id:06d}",
        "timestamp": order.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        "table_number": get_table_label(order.table),
        "items": [
            {
                "name": item.menu_item.name,
                "quantity": item.quantity,
                "unit_price": item.menu_item.price,
                "subtotal": item.quantity * item.menu_item.price,
            }
            for item in order.items
        ],
        "subtotal": order.total_price,
        "tax_amount": round(order.total_price * 0.10, 2),
        "grand_total": round(order.total_price * 1.10, 2),
        "status": order.status.value,
    }
    return voucher_data


# --- GET LIST OF LIVE TABLES ---
@router.get("/tables/active")
def get_active_tables(
    db: Session = Depends(get_db), authenticated: bool = Depends(verify_manager_token)
):
    active_orders = (
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
        .all()
    )

    tables_map = {}
    for order in active_orders:
        if is_counter_table(order.table):
            continue
        t_id = order.table.id
        if t_id not in tables_map:
            tables_map[t_id] = {
                "table_id": t_id,
                "table_number": get_table_label(order.table),
                "active_orders_count": 0,
                "total_price": 0.0,
            }
        tables_map[t_id]["active_orders_count"] += 1
        tables_map[t_id]["total_price"] += order.total_price

    return list(tables_map.values())


# --- WALK-IN / COUNTER SALE ---
@router.post("/counter/sale", response_model=schemas.OrderResponse)
async def create_counter_sale(
    data: schemas.CounterSaleCreate,
    db: Session = Depends(get_db),
    authenticated: bool = Depends(verify_manager_token),
):
    counter_table = (
        db.query(models.RestaurantTable)
        .filter(models.RestaurantTable.number == COUNTER_TABLE_NUMBER)
        .first()
    )
    if not counter_table:
        raise HTTPException(
            status_code=500,
            detail="Counter table is not configured. Restart the server to seed it.",
        )

    order = create_order_from_items(
        db,
        table_id=counter_table.id,
        items=data.items,
        initial_status=models.OrderStatus.PENDING,
    )
    order.settled_at = datetime.now(MYANMAR_TZ).replace(tzinfo=None)
    db.commit()
    order = (
        db.query(models.Order)
        .options(
            joinedload(models.Order.table),
            joinedload(models.Order.items)
            .joinedload(models.OrderItem.menu_item),
            joinedload(models.Order.items)
            .joinedload(models.OrderItem.selected_modifiers)
            .joinedload(models.OrderItemModifier.modifier),
        )
        .filter(models.Order.id == order.id)
        .first()
    )

    response_payload = schemas.OrderResponse.model_validate(order).model_dump(mode="json")
    await manager_ws.broadcast({"event": "new_order", "order": response_payload})

    return order


# --- GENERATE UNIFIED MASTER BILL ---
@router.get("/tables/{table_id}/bill")
def get_consolidated_table_bill(
    table_id: int,
    db: Session = Depends(get_db),
    authenticated: bool = Depends(verify_manager_token),
):
    active_orders = (
        db.query(models.Order)
        .filter(
            models.Order.table_id == table_id,
            models.Order.status.in_(
                [
                    models.OrderStatus.PENDING,
                    models.OrderStatus.PREPARING,
                    models.OrderStatus.SERVED,
                ]
            ),
        )
        .all()
    )

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

            mod_key = "-".join(
                sorted([str(m.modifier_id) for m in item.selected_modifiers])
            )
            item_key = f"{item.menu_item.id}_{mod_key}"

            if item_key not in consolidated_items:
                consolidated_items[item_key] = {
                    "name": item.menu_item.name,
                    "quantity": 0,
                    "unit_price": item_unit_price,
                    "modifiers": [m.modifier.name for m in item.selected_modifiers],
                }

            consolidated_items[item_key]["quantity"] += item.quantity
            grand_total += item_unit_price * item.quantity

    table_num = get_table_label(active_orders[0].table)

    return {
        "restaurant_name": RESTAURANT_NAME,
        "table_id": table_id,
        "table_number": table_num,
        "order_ids": order_ids,
        "timestamp": datetime.now(MYANMAR_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "items": list(consolidated_items.values()),
        "subtotal": grand_total,
        "tax_amount": round(grand_total * 0.10, 2),
        "grand_total": round(grand_total * 1.10, 2),
    }


# --- SETTLE TABLE BILL ---
@router.post("/tables/{table_id}/settle")
async def settle_table_bill(
    table_id: int,
    db: Session = Depends(get_db),
    authenticated: bool = Depends(verify_manager_token),
):
    active_orders = (
        db.query(models.Order)
        .filter(
            models.Order.table_id == table_id,
            models.Order.status.in_(
                [
                    models.OrderStatus.PENDING,
                    models.OrderStatus.PREPARING,
                    models.OrderStatus.SERVED,
                ]
            ),
        )
        .all()
    )

    if not active_orders:
        raise HTTPException(status_code=400, detail="No active orders found to settle.")

    now_settled_time = datetime.now(MYANMAR_TZ).replace(tzinfo=None)

    for order in active_orders:
        order.status = models.OrderStatus.COMPLETED
        order.settled_at = now_settled_time

    db.commit()
    table_label = get_table_label(active_orders[0].table)
    await manager_ws.broadcast(
        {"event": "table_settled", "table_id": table_id, "table_number": table_label}
    )
    return {"message": f"Table {table_label} settled."}


@router.post("/tables/{table_id}/cancel")
async def cancel_table_session(
    table_id: int,
    db: Session = Depends(get_db),
    authenticated: bool = Depends(verify_manager_token),
):
    active_orders = (
        db.query(models.Order)
        .filter(
            models.Order.table_id == table_id,
            models.Order.status.in_(
                [
                    models.OrderStatus.PENDING,
                    models.OrderStatus.PREPARING,
                    models.OrderStatus.SERVED,
                ]
            ),
        )
        .all()
    )

    if not active_orders:
        raise HTTPException(status_code=400, detail="No active orders found to cancel.")

    table_label = get_table_label(active_orders[0].table)

    for order in active_orders:
        security.restore_order_stock(order, db)
        order.status = models.OrderStatus.CANCELLED

    db.commit()

    for order in active_orders:
        db.refresh(order)
        response_payload = schemas.OrderResponse.model_validate(order).model_dump(mode="json")
        await manager_ws.broadcast({"event": "status_update", "order": response_payload})

    return {"message": f"Table {table_label} session cancelled."}


# --- GET TABLE BILLS HISTORY ---
@router.get("/tables/settled-history")
def get_settled_tables_history(
    db: Session = Depends(get_db), authenticated: bool = Depends(verify_manager_token)
):
    completed_orders = (
        db.query(models.Order)
        .filter(
            models.Order.status != models.OrderStatus.CANCELLED,
            models.Order.settled_at != None,
        )
        .order_by(models.Order.settled_at.desc())
        .all()
    )

    history_map = {}
    for order in completed_orders:
        iso_time = order.settled_at.isoformat()
        key = f"{order.table_id}_{iso_time}"

        if key not in history_map:
            history_map[key] = {
                "table_id": order.table_id,
                "table_number": get_table_label(order.table),
                "is_counter": is_counter_table(order.table),
                "settled_at": order.settled_at.strftime("%Y-%m-%d %H:%M:%S"),
                "settled_at_iso": iso_time,
                "order_ids": [],
                "total_price": 0.0,
            }
        history_map[key]["order_ids"].append(order.id)
        history_map[key]["total_price"] += order.total_price

    return list(history_map.values())


# --- DYNAMICALLY RECONSTRUCT COMPLETED TABLE BILLS ---
@router.get("/tables/{table_id}/historical-bill")
def get_historical_table_bill(
    table_id: int,
    settled_at: str,
    db: Session = Depends(get_db),
    authenticated: bool = Depends(verify_manager_token),
):
    try:
        settled_datetime = datetime.fromisoformat(settled_at)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ISO date format.")

    orders = (
        db.query(models.Order)
        .filter(
            models.Order.table_id == table_id,
            models.Order.status != models.OrderStatus.CANCELLED,
            models.Order.settled_at == settled_datetime,
        )
        .all()
    )

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

            mod_key = "-".join(
                sorted([str(m.modifier_id) for m in item.selected_modifiers])
            )
            item_key = f"{item.menu_item.id}_{mod_key}"

            if item_key not in consolidated_items:
                consolidated_items[item_key] = {
                    "name": item.menu_item.name,
                    "quantity": 0,
                    "unit_price": item_unit_price,
                    "modifiers": [m.modifier.name for m in item.selected_modifiers],
                }

            consolidated_items[item_key]["quantity"] += item.quantity
            grand_total += item_unit_price * item.quantity

    table_num = get_table_label(orders[0].table)

    return {
        "restaurant_name": RESTAURANT_NAME,
        "table_id": table_id,
        "table_number": table_num,
        "order_ids": order_ids,
        "timestamp": datetime.now(MYANMAR_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "items": list(consolidated_items.values()),
        "subtotal": grand_total,
        "tax_amount": round(grand_total * 0.10, 2),
        "grand_total": round(grand_total * 1.10, 2),
    }


# --- DAILY FINANCIAL ANALYTICS ---
@router.get("/analytics/daily", response_model=schemas.DailyAnalytics)
def get_daily_analytics(
    date: Optional[str] = None,
    db: Session = Depends(get_db),
    authenticated: bool = Depends(verify_manager_token),
):
    if date:
        try:
            target_date = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(
                status_code=400, detail="Invalid date format. Use YYYY-MM-DD."
            )
    else:
        target_date = datetime.now(MYANMAR_TZ).date()

    day_start = datetime.combine(target_date, time.min)
    day_end = datetime.combine(target_date, time.max)

    # Ops metrics only — revenue/income lives in the finance panel
    completed_orders = (
        db.query(models.Order)
        .filter(
            models.Order.created_at.between(day_start, day_end),
            models.Order.status == models.OrderStatus.COMPLETED,
        )
        .all()
    )

    total_completed = len(completed_orders)

    popular_items = (
        db.query(
            models.MenuItem.name,
            func.sum(models.OrderItem.quantity).label("total_sold"),
        )
        .join(models.OrderItem, models.MenuItem.id == models.OrderItem.menu_item_id)
        .join(models.Order, models.Order.id == models.OrderItem.order_id)
        .filter(
            models.Order.created_at.between(day_start, day_end),
            models.Order.status == models.OrderStatus.COMPLETED,
        )
        .group_by(models.MenuItem.name)
        .order_by(func.sum(models.OrderItem.quantity).desc())
        .limit(5)
        .all()
    )

    top_selling = [{"name": item[0], "sold_qty": item[1]} for item in popular_items]

    return schemas.DailyAnalytics(
        date=target_date.strftime("%Y-%m-%d"),
        total_orders_completed=total_completed,
        top_selling_items=top_selling,
    )


# Revenue PDF export moved to /api/v1/finance/export (finance panel only)
@router.get("/analytics/export")
def export_daily_report_removed(
    authenticated: bool = Depends(verify_manager_token),
):
    raise HTTPException(
        status_code=403,
        detail="Revenue reports are only available in the Finance panel (/finance).",
    )


@router.websocket("/ws")
async def manager_websocket_endpoint(websocket: WebSocket):
    token = websocket.query_params.get("token")
    if not security.verify_manager_ws_token(token):
        await websocket.close(code=1008, reason="Manager authentication required")
        return

    await manager_ws.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"LOG: [Manager WebSocket] Unexpected connection break: {e}")
    finally:
        manager_ws.disconnect(websocket)