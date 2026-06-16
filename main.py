from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import models, os
from database import engine, SessionLocal
from routers import menu, kitchen, manager

models.Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="QR Restaurant Ordering System",
    description="Secure dynamic restaurant management operations engine with real-time analytics."
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routing Modules
app.include_router(menu.router, prefix="/api/v1")
app.include_router(kitchen.router, prefix="/api/v1")
app.include_router(manager.router, prefix="/api/v1")

# Static Frontend Routes
@app.get("/menu")
def serve_menu():
    file_path = os.path.join("static", "menu.html")
    return FileResponse(file_path) if os.path.exists(file_path) else {"error": "menu.html missing"}

@app.get("/kitchen")
def serve_kitchen():
    file_path = os.path.join("static", "kitchen.html")
    return FileResponse(file_path) if os.path.exists(file_path) else {"error": "kitchen.html missing"}

@app.get("/manager")
def serve_manager():
    file_path = os.path.join("static", "manager.html")
    return FileResponse(file_path) if os.path.exists(file_path) else {"error": "manager.html missing"}

@app.on_event("startup")
def seed_initial_data():
    db = SessionLocal()
    # Seed tables 1-10
    if not db.query(models.RestaurantTable).first():
        tables = [models.RestaurantTable(number=i) for i in range(1, 11)]
        db.add_all(tables)
        db.commit()

    # Seed complex items with modifiers & stock limits
    if not db.query(models.MenuItem).first():
        americano = models.MenuItem(name="Americano", description="Americano", price=5000, category="Coffee Classics", stock=25)
        espresso = models.MenuItem(name="Espresso", description="Espresso", price=5000, category="Coffee Classics", stock=50)
        cappuccino = models.MenuItem(name="Cappuccino", description="Cappuccino", price=5000, category="Coffee Classics", stock=10)

        db.add_all([americano, espresso, cappuccino])
        db.flush()

        # Seed custom item modifiers
        #mod1 = models.MenuItemModifier(menu_item_id=burger.id, name="Double Meat Patty", price=2.50)
        #mod2 = models.MenuItemModifier(menu_item_id=burger.id, name="Add Smoked Bacon", price=1.50)
        #mod3 = models.MenuItemModifier(menu_item_id=fries.id, name="Add Extra Cheese sauce", price=0.75)
        
        #db.add_all([mod1, mod2, mod3])
        db.commit()
    db.close()

@app.get("/")
def root():
    return {"message": "Dine Inn System backend is active. Load /menu, /kitchen or /manager."}