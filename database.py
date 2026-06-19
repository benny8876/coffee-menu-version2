import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# 1. Environment Variable ကနေ ဖတ်မယ်
SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./restaurant.db")

# 🔥 ဒီအဆင့်ကို တိုးထည့်လိုက်တာပါ!
# တကယ်လို့ လင့်ခ်က postgres:// နဲ့ စနေရင် postgresql:// ဖြစ်အောင် အော်တို လဲပေးမယ်
if SQLALCHEMY_DATABASE_URL.startswith("postgres://"):
    SQLALCHEMY_DATABASE_URL = SQLALCHEMY_DATABASE_URL.replace("postgres://", "postgresql://", 1)

# 2. ဒေတာဘေ့စ် အင်ဂျင်ဆောက်ခြင်း
if SQLALCHEMY_DATABASE_URL.startswith("sqlite"):
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
    )
else:
    engine = create_engine(SQLALCHEMY_DATABASE_URL)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()