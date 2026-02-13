"""
/start command handler
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from database.crud import get_or_create_user, get_or_create_session, update_session_state
from handlers.screen_renderer import render_screen


def handle_start(bot, update):
    """
    Handle /start command.
    New users → SCR_WELCOME
    Returning users → SCR_HUB
    Deep link codes (e.g. /start CH_123) → Start Challenge
    """
    telegram_id = update.effective_user.id
    username = update.effective_user.username
    full_name = update.effective_user.full_name or update.effective_user.first_name
    
    # Check for deep link parameters
    args = update.message.text.split(" ", 1)
    param = args[1] if len(args) > 1 else None
    
    user = get_or_create_user(telegram_id, username, full_name)
    session = get_or_create_session(user.id)
    
    # Handle Multiplayer Deep Links
    if param and param.startswith("CH_"):
        from database.crud import get_challenge
        from handlers.game_handler import start_challenge_session
        print(f"DEBUG: Start command with param: {param}")
        challenge = get_challenge(param)
        if challenge:
             print(f"DEBUG: Found challenge {param}, starting session...")
             update_session_state(user.id, screen="SCR_GAME_PRES", current_param=param)
             start_challenge_session(bot, telegram_id, challenge)
             return # Stop here! Do not go to Home.
        else:
             print(f"DEBUG: Challenge {param} not found.")
             bot.send_message(chat_id=telegram_id, text="❌ Challenge not found or expired.")
             return # Stop here! Do not go to Home.

    # Normal Flow
    import datetime
    is_new = (datetime.datetime.utcnow() - user.join_date).total_seconds() < 5
    screen_id = "SCR_WELCOME" if is_new else "SCR_HUB"
    
    message = render_screen(bot, user.id, telegram_id, screen_id, message_id=None)
    update_session_state(user.id, screen=screen_id, message_id=message.message_id, add_to_nav_stack=False)
    return message
