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


class CounterSaleCreate(BaseModel):
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
    label: str
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
    total_orders_completed: int
    top_selling_items: List[dict]


# --- Finance / Expenses ---
EXPENSE_CATEGORIES = [
    "Supplies",
    "Rent",
    "Utilities",
    "Staff",
    "Equipment",
    "Marketing",
    "Other",
]

EXPENSE_CATEGORY_MYANMAR = {
    "Supplies": "ပစ္စည်းများ",
    "Rent": "ငှားရမ်းခ",
    "Utilities": "အသုံးအဆောင်ခ",
    "Staff": "ဝန်ထမ်းစရိတ်",
    "Equipment": "စက်ပစ္စည်း",
    "Marketing": "ကြော်ငြာစရိတ်",
    "Other": "အခြား",
}


class ExpenseBase(BaseModel):
    category: str
    amount: float = Field(..., gt=0)
    description: Optional[str] = None
    recorded_at: Optional[datetime] = None


class ExpenseCreate(ExpenseBase):
    pass


class ExpenseUpdate(BaseModel):
    category: Optional[str] = None
    amount: Optional[float] = Field(None, gt=0)
    description: Optional[str] = None
    recorded_at: Optional[datetime] = None


class ExpenseResponse(ExpenseBase):
    id: int
    recorded_at: datetime

    class Config:
        from_attributes = True


class FinanceIncomeEntry(BaseModel):
    order_id: int
    table_label: str
    amount: float
    created_at: datetime
    settled_at: Optional[datetime] = None
    status: OrderStatus


class FinanceSummary(BaseModel):
    date: str
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    period_label: Optional[str] = None
    income_total: float
    outcome_total: float
    net_profit: float
    monthly_income: float
    monthly_outcome: float
    monthly_net: float
    order_count: int
    expense_count: int
    income_entries: List[FinanceIncomeEntry]
    expenses: List[ExpenseResponse]
    expenses_by_category: List[dict]