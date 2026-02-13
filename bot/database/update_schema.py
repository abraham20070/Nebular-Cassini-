"""
Simple script to update database schema with new columns
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from database.db import init_db

if __name__ == "__main__":
    print("[INFO] Updating database schema...")
    init_db()
    print("[OK] Database schema updated!")
