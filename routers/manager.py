from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Header
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, time, timezone, timedelta
from typing import Optional, List
import csv, io, hashlib, shutil, uuid
import os
from database import get_db
import models, schemas
import security
from table_labels import RESTAURANT_NAME, get_table_label
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

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
    admin = (
        db.query(models.AdminCredential)
        .filter(models.AdminCredential.username == credentials.username)
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
    admin = db.query(models.AdminCredential).first()
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
        .filter(models.RestaurantTable.is_active == True)
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
def settle_table_bill(
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
    return {"message": f"Table {table_label} settled."}


# --- GET TABLE BILLS HISTORY ---
@router.get("/tables/settled-history")
def get_settled_tables_history(
    db: Session = Depends(get_db), authenticated: bool = Depends(verify_manager_token)
):
    completed_orders = (
        db.query(models.Order)
        .filter(
            models.Order.status == models.OrderStatus.COMPLETED,
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
            models.Order.status == models.OrderStatus.COMPLETED,
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

    # 1. Daily query
    completed_orders = (
        db.query(models.Order)
        .filter(
            models.Order.created_at.between(day_start, day_end),
            models.Order.status == models.OrderStatus.COMPLETED,
        )
        .all()
    )

    total_revenue = sum(order.total_price for order in completed_orders)
    total_completed = len(completed_orders)

    # 2. NEW: Calculate boundary limits for the entire month
    month_start = datetime(target_date.year, target_date.month, 1, 0, 0, 0)
    if target_date.month == 12:
        next_month_start = datetime(target_date.year + 1, 1, 1, 0, 0, 0)
    else:
        next_month_start = datetime(target_date.year, target_date.month + 1, 1, 0, 0, 0)
    month_end = next_month_start - timedelta(seconds=1)

    # Query all completed orders within this month
    monthly_orders = (
        db.query(models.Order)
        .filter(
            models.Order.created_at.between(month_start, month_end),
            models.Order.status == models.OrderStatus.COMPLETED,
        )
        .all()
    )
    total_monthly_revenue = sum(order.total_price for order in monthly_orders)

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
        total_revenue=total_revenue,
        total_monthly_revenue=total_monthly_revenue,  # NEW
        total_orders_completed=total_completed,
        top_selling_items=top_selling,
    )


# --- Updated: PDF Business Summary Exporter ---
@router.get("/analytics/export")
def export_daily_report(
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

    # 1. Fetch Analytics Data
    completed_orders = (
        db.query(models.Order)
        .filter(
            models.Order.created_at.between(day_start, day_end),
            models.Order.status == models.OrderStatus.COMPLETED,
        )
        .all()
    )

    total_revenue = sum(order.total_price for order in completed_orders)
    total_transactions = len(completed_orders)

    # 2. Group Table Performance (Tables 1 - 10)
    tables = (
        db.query(models.RestaurantTable)
        .order_by(models.RestaurantTable.number.asc())
        .all()
    )
    table_revenue = {get_table_label(t): 0.0 for t in tables}
    for order in completed_orders:
        t_label = get_table_label(order.table)
        table_revenue[t_label] = table_revenue.get(t_label, 0.0) + order.total_price

    # 3. Aggregate Top Selling Products
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
        .all()
    )

    # --- REPORTLAB PDF GENERATION ENGINE ---
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=40,
        leftMargin=40,
        topMargin=40,
        bottomMargin=40,
    )

    story = []
    styles = getSampleStyleSheet()

    # Define palette matching 27 Cafe
    primary_color = colors.HexColor("#301f16")  # Elegant Deep Brown
    secondary_color = colors.HexColor("#6f8a38")  # Soft Olive Green
    neutral_light = colors.HexColor("#fbfbfa")

    # Typography Styling
    title_style = ParagraphStyle(
        "DocTitle",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=20,
        leading=24,
        textColor=primary_color,
        spaceAfter=4,
    )
    subtitle_style = ParagraphStyle(
        "DocSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica-Oblique",
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#666666"),
        spaceAfter=15,
    )
    section_heading_style = ParagraphStyle(
        "SectionHeading",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=15,
        textColor=secondary_color,
        spaceBefore=12,
        spaceAfter=6,
    )
    cell_text_style = ParagraphStyle(
        "CellText",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#333333"),
    )
    cell_bold_style = ParagraphStyle(
        "CellBold",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#161912"),
    )
    th_style = ParagraphStyle(
        "TableHeader",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=11,
        textColor=colors.white,
    )

    # Block A: Header Logo & Subtitle
    story.append(Paragraph("DAILY BUSINESS SUMMARY REPORT", title_style))
    story.append(
        Paragraph(
            f"Date: {target_date.strftime('%Y-%m-%d')}  |  Timezone: Asia/Yangon (Myanmar)  |  Generated dynamically by 27 Cafe POS",
            subtitle_style,
        )
    )
    story.append(Spacer(1, 10))

    # Block B: Metrics Summary Table
    story.append(Paragraph("1. Performance Metrics Summary", section_heading_style))
    metrics_data = [
        [Paragraph("Metric Description", th_style), Paragraph("Value", th_style)],
        [
            Paragraph("Total Daily Revenue", cell_text_style),
            Paragraph(f"{total_revenue:.2f} Ks", cell_bold_style),
        ],
        [
            Paragraph("Total Completed Transactions", cell_text_style),
            Paragraph(str(total_transactions), cell_bold_style),
        ],
    ]
    t_metrics = Table(metrics_data, colWidths=[250, 150])
    t_metrics.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), primary_color),
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#dddddd")),
                (
                    "ROWBACKGROUNDS",
                    (0, 1),
                    (-1, -1),
                    [colors.white, colors.HexColor("#f9f9f9")],
                ),
            ]
        )
    )
    story.append(t_metrics)
    story.append(Spacer(1, 15))

    # Block C: Table-by-Table Sales Table
    story.append(Paragraph("2. Revenue Generated by Table", section_heading_style))
    table_data = [
        [
            Paragraph("Table Number", th_style),
            Paragraph("Accumulated Sales (Ks)", th_style),
        ]
    ]
    for t_label, rev in sorted(table_revenue.items()):
        table_data.append(
            [
                Paragraph(f"Table {t_label}", cell_text_style),
                Paragraph(
                    f"{rev:.2f}Ks", cell_bold_style if rev > 0 else cell_text_style
                ),
            ]
        )
    t_tables = Table(table_data, colWidths=[200, 200])
    t_tables.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), secondary_color),
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#dddddd")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, neutral_light]),
            ]
        )
    )
    story.append(t_tables)
    story.append(Spacer(1, 15))

    # Block D: Top Selling Products Table
    story.append(
        Paragraph(
            "3. Product Popularity (Top Selling Menu Items)", section_heading_style
        )
    )
    items_data = [
        [Paragraph("Item Name", th_style), Paragraph("Quantity Sold", th_style)]
    ]
    if not popular_items:
        items_data.append(
            [
                Paragraph("No items sold on this date", cell_text_style),
                Paragraph("0", cell_text_style),
            ]
        )
    else:
        for item in popular_items:
            items_data.append(
                [
                    Paragraph(item[0], cell_text_style),
                    Paragraph(str(item[1]), cell_bold_style),
                ]
            )

    t_items = Table(items_data, colWidths=[250, 150])
    t_items.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), primary_color),
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#dddddd")),
                (
                    "ROWBACKGROUNDS",
                    (0, 1),
                    (-1, -1),
                    [colors.white, colors.HexColor("#f9f9f9")],
                ),
            ]
        )
    )
    story.append(t_items)

    # Build the document
    doc.build(story)
    buffer.seek(0)

    filename = f"business_summary_{target_date.strftime('%Y-%m-%d')}.pdf"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return StreamingResponse(buffer, media_type="application/pdf", headers=headers)