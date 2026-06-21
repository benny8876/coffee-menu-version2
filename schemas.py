from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime
from models import OrderStatus

# --- NEW: Administrator Login Schema ---
class LoginRequest(BaseModel):
    username: str
    password: str

# --- NEW: Password Change Schema ---
class PasswordChangeRequest(BaseModel):
    old_password: str
    new_password: str

class KitchenLoginRequest(BaseModel):
    pin: str

class MockPayRequest(BaseModel):
    table_id: int
    token: str

# --- Modifiers ---
class ModifierBase(BaseModel):
    name: str
    price: float = Field(..., ge=0)

class ModifierCreate(ModifierBase):
    pass

class ModifierResponse(ModifierBase):
    id: int
    class Config:
        from_attributes = True

# --- Menu Items ---
class MenuItemBase(BaseModel):
    name: str
    description: Optional[str] = None
    price: float = Field(..., gt=0)
    category: str
    is_available: bool = True
    stock: Optional[int] = None
    image_url: Optional[str] = None
    order_index: Optional[int] = None # NEW: Tracks display order index

class MenuItemCreate(MenuItemBase):
    modifiers: Optional[List[ModifierCreate]] = []

class MenuItemUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    price: Optional[float] = Field(None, gt=0)
    category: Optional[str] = None
    is_available: Optional[bool] = None
    stock: Optional[int] = None
    
    # NEW
    image_url: Optional[str] = None 

class MenuItemResponse(MenuItemBase):
    id: int
    modifiers: List[ModifierResponse] = []
    class Config:
        from_attributes = True

# --- Orders ---
class OrderItemModifierResponse(BaseModel):
    modifier: ModifierResponse
    class Config:
        from_attributes = True

class OrderItemCreate(BaseModel):
    menu_item_id: int
    quantity: int = Field(..., gt=0)
    notes: Optional[str] = None
    modifier_ids: Optional[List[int]] = []

class OrderCreate(BaseModel):
    table_id: int
    token: str # Security validation token
    items: List[OrderItemCreate]

class OrderItemResponse(BaseModel):
    id: int
    menu_item: MenuItemResponse
    quantity: int
    notes: Optional[str] = None
    selected_modifiers: List[OrderItemModifierResponse] = []
    class Config:
        from_attributes = True

class TableResponse(BaseModel):
    id: int
    number: int
    class Config:
        from_attributes = True

class OrderResponse(BaseModel):
    id: int
    table: TableResponse
    status: OrderStatus
    total_price: float
    created_at: datetime
    items: List[OrderItemResponse]
    class Config:
        from_attributes = True

class DailyAnalytics(BaseModel):
    date: str
    total_revenue: float
    total_monthly_revenue: float # NEW: Tracks total revenue for the selected calendar month
    total_orders_completed: int
    top_selling_items: List[dict]