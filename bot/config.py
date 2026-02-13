"""
Nebular Cassini Bot Configuration
"""
import os
from dotenv import load_dotenv

load_dotenv()

# Telegram Bot
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# Admin Access
ADMIN_IDS_STR = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS_STR.split(",") if x.strip()]

# Database
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///nebular_cassini_v2.db")

# Data Directory
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")

# Phase Mastery Thresholds
PHASE_UNLOCK_THRESHOLD = 80.0  # 80% accuracy required to unlock next phase
