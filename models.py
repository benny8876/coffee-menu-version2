import enum
from sqlalchemy import Column, Integer, String, Float, Boolean, ForeignKey, DateTime, Enum
from sqlalchemy.orm import relationship
from datetime import datetime, timezone, timedelta
from database import Base


# --- MYANMAR (YANGON) TIMEZONE OFFSET CONFIGURATION ---
MYANMAR_TZ = timezone(timedelta(hours=6, minutes=30))

def get_yangon_now():
    # Returns the exact current time in Myanmar (UTC+6:30) as a database-friendly naive object
    return datetime.now(MYANMAR_TZ).replace(tzinfo=None)

class OrderStatus(str, enum.Enum):
    AWAITING_PAYMENT = "awaiting_payment" # Payment Hold
    PENDING = "pending"                   # Paid & Queued
    PREPARING = "preparing"               # In Kitchen
    SERVED = "served"                     # Served to Table
    COMPLETED = "completed"               # Cleared and settled
    CANCELLED = "cancelled"

class RestaurantTable(Base):
    __tablename__ = "tables"
    id = Column(Integer, primary_key=True, index=True)
    number = Column(Integer, unique=True, nullable=False)
    is_active = Column(Boolean, default=True)

class MenuItem(Base):
    __tablename__ = "menu_items"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    price = Column(Float, nullable=False)
    category = Column(String, nullable=False)
    is_available = Column(Boolean, default=True)
    stock = Column(Integer, nullable=True)
    
    # NEW: Stores the server path of your uploaded menu item photos
    image_url = Column(String, nullable=True) 

    modifiers = relationship("MenuItemModifier", back_populates="menu_item", cascade="all, delete-orphan")

class MenuItemModifier(Base):
    __tablename__ = "menu_item_modifiers"
    id = Column(Integer, primary_key=True, index=True)
    menu_item_id = Column(Integer, ForeignKey("menu_items.id"), nullable=False)
    name = Column(String, nullable=False)
    price = Column(Float, nullable=False, default=0.0)

    menu_item = relationship("MenuItem", back_populates="modifiers")



class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True, index=True)
    table_id = Column(Integer, ForeignKey("tables.id"), nullable=False)
    status = Column(Enum(OrderStatus), default=OrderStatus.AWAITING_PAYMENT)
    total_price = Column(Float, default=0.0)
    created_at = Column(DateTime, default=get_yangon_now)
    
    # NEW: Tracks when the grouped table session was formally closed and paid
    settled_at = Column(DateTime, nullable=True) 

    table = relationship("RestaurantTable")
    items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")



class OrderItem(Base):
    __tablename__ = "order_items"
    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    menu_item_id = Column(Integer, ForeignKey("menu_items.id"), nullable=False)
    quantity = Column(Integer, nullable=False)
    notes = Column(String, nullable=True)

    order = relationship("Order", back_populates="items")
    menu_item = relationship("MenuItem")
    selected_modifiers = relationship("OrderItemModifier", back_populates="order_item", cascade="all, delete-orphan")

class OrderItemModifier(Base):
    __tablename__ = "order_item_modifiers"
    id = Column(Integer, primary_key=True, index=True)
    order_item_id = Column(Integer, ForeignKey("order_items.id"), nullable=False)
    modifier_id = Column(Integer, ForeignKey("menu_item_modifiers.id"), nullable=False)

    order_item = relationship("OrderItem", back_populates="selected_modifiers")
    modifier = relationship("MenuItemModifier")


class AdminCredential(Base):
    __tablename__ = "admin_credentials"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)