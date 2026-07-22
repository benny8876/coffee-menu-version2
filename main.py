from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import models, os
from sqlalchemy import inspect, text
from database import engine, SessionLocal
from table_labels import TABLE_LABELS, label_for_number, COUNTER_TABLE_NUMBER, COUNTER_TABLE_LABEL
from routers import menu, kitchen, manager, finance
import hashlib  # Add to imports

models.Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="QR Restaurant Ordering System",
    description="Secure dynamic restaurant management operations engine with real-time analytics.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# NEW: Mount the static directory to serve static media files dynamically
app.mount("/static", StaticFiles(directory="static"), name="static")


# Routing Modules
app.include_router(menu.router, prefix="/api/v1")
app.include_router(kitchen.router, prefix="/api/v1")
app.include_router(manager.router, prefix="/api/v1")
app.include_router(finance.router, prefix="/api/v1")

# Static Frontend Routes
@app.get("/menu")
def serve_menu():
    return FileResponse(os.path.join("static", "menu.html"))


@app.get("/kitchen")
def serve_kitchen():
    return FileResponse(os.path.join("static", "kitchen.html"))


@app.get("/manager")
def serve_manager():
    return FileResponse(os.path.join("static", "manager.html"))


@app.get("/finance")
def serve_finance():
    return FileResponse(os.path.join("static", "finance.html"))


# Simple zero-dependency secure password hasher
def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def migrate_table_labels():
    inspector = inspect(engine)
    if "tables" not in inspector.get_table_names():
        return

    columns = [col["name"] for col in inspector.get_columns("tables")]
    if "label" not in columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE tables ADD COLUMN label VARCHAR"))

    db = SessionLocal()
    try:
        tables = db.query(models.RestaurantTable).all()
        for table in tables:
            if not table.label:
                table.label = label_for_number(table.number)
        db.commit()
    finally:
        db.close()


def ensure_counter_table(db):
    counter = (
        db.query(models.RestaurantTable)
        .filter(models.RestaurantTable.number == COUNTER_TABLE_NUMBER)
        .first()
    )
    if not counter:
        db.add(
            models.RestaurantTable(
                number=COUNTER_TABLE_NUMBER,
                label=COUNTER_TABLE_LABEL,
                is_active=True,
            )
        )
        db.commit()
    elif counter.label != COUNTER_TABLE_LABEL:
        counter.label = COUNTER_TABLE_LABEL
        db.commit()


@app.on_event("startup")
def seed_initial_data():
    migrate_table_labels()
    db = SessionLocal()

    # Seed manager account (staff panel)
    if not db.query(models.AdminCredential).filter(
        models.AdminCredential.username == "admin"
    ).first():
        db.add(
            models.AdminCredential(
                username="admin", password_hash=hash_password("adminpassword123")
            )
        )
        db.commit()

    # Seed finance account (owner panel) — separate password from manager
    if not db.query(models.AdminCredential).filter(
        models.AdminCredential.username == "finance"
    ).first():
        db.add(
            models.AdminCredential(
                username="finance", password_hash=hash_password("adminpassword123")
            )
        )
        db.commit()

    # Seed tables A1–A7 and B1–B6
    if not db.query(models.RestaurantTable).first():
        tables = [
            models.RestaurantTable(number=i + 1, label=label)
            for i, label in enumerate(TABLE_LABELS)
        ]
        db.add_all(tables)
        db.commit()

    ensure_counter_table(db)

    # Seed default menu items
    if not db.query(models.MenuItem).first():
        # ၁။ Item တွေကို အရင်ဆောက်ပါ
        burger = models.MenuItem(
            name="Cheeseburger", price=8.99, category="Main", stock=25
        )
        fries = models.MenuItem(
            name="French Fries", price=3.49, category="Side", stock=50
        )
        soda = models.MenuItem(name="Iced Soda", price=2.49, category="Drink", stock=10)
        # Coffee ကိုလည်း အောက်မှာ ထည့်ပေးပါ
        coffee = models.MenuItem(
            name="Iced Coffee", price=3.00, category="Drink", stock=20
        )

        db.add_all([burger, fries, soda, coffee])
        db.flush()  # ID တွေ ရဖို့အတွက် flush ခံပေးပါ

        # ၂။ အခုမှ coffee.id ကို သုံးလို့ရပါပြီ
        #mod_less_sugar = models.MenuItemModifier(
            #menu_item_id=coffee.id, name="Less Sugar", price=0.0
        #)
        #mod_more_sugar = models.MenuItemModifier(
            #menu_item_id=coffee.id, name="More Sugar", price=0.0
        #)
        #mod_less_ice = models.MenuItemModifier(
            #menu_item_id=coffee.id, name="Less Ice", price=0.0
        #)
        #mod_more_ice = models.MenuItemModifier(
            #menu_item_id=coffee.id, name="More Ice", price=0.0
        #)

        #db.add_all([mod_less_sugar, mod_more_sugar, mod_less_ice, mod_more_ice])
        db.commit()


@app.get("/")
def root():
    return {
        "message": "Dine Inn System backend is active. Load /menu, /kitchen, /manager or /finance."
    }


if __name__ == "__main__":
    import uvicorn

    # Coolify ရဲ့ internal port ဖြစ်တဲ့ 3000 ပေါ်မှာ မောင်းပေးလိုက်တာပါ
    uvicorn.run("main:app", host="0.0.0.0", port=3000)
