"""
Database connection and initialization
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session
from .models import Base
import os
import sys

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import DATABASE_URL

# Create engine
engine = create_engine(
    DATABASE_URL,
    echo=False,  # Set to True for SQL debug logging
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {},
    pool_pre_ping=True,      # Check connection before using it
    pool_recycle=300         # Recycle connections every 5 minutes
)

# Create session factory
SessionFactory = sessionmaker(bind=engine)
SessionLocal = scoped_session(SessionFactory)


def init_db():
    """Initialize database - create all tables"""
    Base.metadata.create_all(bind=engine)
    print("[OK] Database initialized successfully")



def get_db():
    """Get database session (for dependency injection)"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def close_db():
    """Close database connection"""
    SessionLocal.remove()
