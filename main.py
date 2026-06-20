from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import models, os
from database import engine, SessionLocal
from routers import menu, kitchen, manager
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


# Simple zero-dependency secure password hasher
def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


@app.on_event("startup")
def seed_initial_data():
    db = SessionLocal()

    # NEW: Seeds default manager credentials on first database creation
    if not db.query(models.AdminCredential).first():
        default_admin = models.AdminCredential(
            username="admin", password_hash=hash_password("adminpassword123")
        )
        db.add(default_admin)
        db.commit()

    # Seed tables 1-10
    if not db.query(models.RestaurantTable).first():
        tables = [models.RestaurantTable(number=i) for i in range(1, 14)]
        db.add_all(tables)
        db.commit()

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
        #db.commit()


@app.get("/")
def root():
    return {
        "message": "Dine Inn System backend is active. Load /menu, /kitchen or /manager."
    }


if __name__ == "__main__":
    import uvicorn

    # Coolify ရဲ့ internal port ဖြစ်တဲ့ 3000 ပေါ်မှာ မောင်းပေးလိုက်တာပါ
    uvicorn.run("main:app", host="0.0.0.0", port=3000)
