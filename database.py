import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# 1. Environment Variable ကနေ DATABASE_URL ကို လှမ်းဖတ်မယ်
# တကယ်လို့ မရှိခဲ့ရင် (Local စက်ထဲမှာဆိုရင်) SQLite ကို အော်တို သုံးသွားမယ်
SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./restaurant.db")

# 2. PostgreSQL အတွက်ဆိုရင် check_same_thread မလိုတဲ့အတွက် ခွဲပေးရပါမယ်
if SQLALCHEMY_DATABASE_URL.startswith("sqlite"):
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
    )
else:
    engine = create_engine(SQLALCHEMY_DATABASE_URL)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

# Dependency to get db session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()