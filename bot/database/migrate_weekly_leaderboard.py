"""
Migration script to add weekly leaderboard support
Adds weekly_xp and week_start_date columns to users table
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from database.db import engine
from sqlalchemy import text
from datetime import datetime

def migrate():
    """Add weekly leaderboard columns to users table"""
    print("[MIGRATION] Adding weekly leaderboard support...")
    
    with engine.connect() as conn:
        try:
            # Check if columns already exist
            result = conn.execute(text("PRAGMA table_info(users)"))
            columns = [row[1] for row in result]
            
            if 'weekly_xp' not in columns:
                print("[MIGRATION] Adding weekly_xp column...")
                conn.execute(text("ALTER TABLE users ADD COLUMN weekly_xp INTEGER DEFAULT 0 NOT NULL"))
                conn.commit()
                print("[OK] weekly_xp column added")
            else:
                print("[SKIP] weekly_xp column already exists")
            
            if 'week_start_date' not in columns:
                print("[MIGRATION] Adding week_start_date column...")
                # SQLite doesn't support DATETIME directly in ALTER TABLE, use TEXT
                conn.execute(text(f"ALTER TABLE users ADD COLUMN week_start_date DATETIME DEFAULT '{datetime.utcnow().isoformat()}' NOT NULL"))
                conn.commit()
                print("[OK] week_start_date column added")
            else:
                print("[SKIP] week_start_date column already exists")
            
            print("[OK] Migration completed successfully!")
            
        except Exception as e:
            print(f"[ERROR] Migration failed: {e}")
            raise

if __name__ == "__main__":
    migrate()
