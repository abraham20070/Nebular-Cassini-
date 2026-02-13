"""
Nebular Cassini Telegram Bot - Main Entry Point
"""
import sys
import os

# Add current directory to path
sys.path.insert(0, os.path.dirname(__file__))

from telegram.ext import Updater, CommandHandler, CallbackQueryHandler
from config import BOT_TOKEN
from database import init_db
from handlers import handle_start, route_callback
from keep_alive import keep_alive
from telegram.ext import JobQueue


def main():
    """Start the bot"""
    keep_alive() # Start pinger server for 24/7 uptime
    print("=" * 60)
    print("NEBULAR CASSINI BOT - Starting...")
    print("=" * 60)
    
    # Initialize database
    print("\n[1/3] Initializing database...")
    init_db()
    
    # Create updater and dispatcher
    print("[2/3] Connecting to Telegram...")
    if not BOT_TOKEN:
        print("[ERROR] TELEGRAM_BOT_TOKEN not set in .env file!")
        return
    
    updater = Updater(BOT_TOKEN, use_context=False)
    dispatcher = updater.dispatcher
    
    # Attach job_queue to bot for access in handlers
    updater.bot.job_queue = updater.job_queue
    
    # Register handlers
    print("[3/3] Registering handlers...")
    dispatcher.add_handler(CommandHandler("start", handle_start))
    dispatcher.add_handler(CallbackQueryHandler(route_callback))
    
    # Start the job queue
    if updater.job_queue:
        updater.job_queue.start()
        print("[INFO] JobQueue started successfully.")
    
    # Start polling
    print("\n" + "=" * 60)
    print("[OK] Bot is running! Press Ctrl+C to stop.")
    print("=" * 60)
    print("\nWaiting for messages...")
    
    updater.start_polling()
    updater.idle()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n[OK] Bot stopped by user")
    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()
