"""
Navigation helpers - handle screen transitions and back button logic
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from database.crud import get_or_create_user, update_session_state, pop_navigation_stack, get_or_create_session
from handlers.screen_renderer import render_screen


def navigate_to(bot, telegram_id, screen_id, param=None, add_to_stack=True, extra_vars=None):
    """
    Navigate to a new screen.
    """
    # Get or create user
    user = get_or_create_user(telegram_id, None, "User")
    
    # Get current session
    session = get_or_create_session(user.id)
    
    # Merge param into extra_vars if provided
    if extra_vars is None:
        extra_vars = {}
    if param:
        extra_vars["param"] = param
    
    # Render the screen
    message = render_screen(
        bot, 
        user.id, 
        telegram_id, 
        screen_id, 
        message_id=session.last_message_id,
        extra_vars=extra_vars if extra_vars else None
    )
    
    # If message is None, it means the content was identical (no update needed)
    if message is None:
        return None

    # Update session state with the new screen AND its parameter
    update_session_state(
        user.id,
        screen=screen_id,
        current_param=param,
        message_id=message.message_id,
        add_to_nav_stack=add_to_stack
    )
    
    return message


def go_back(bot, telegram_id):
    """
    Navigate to previous screen using navigation stack.
    """
    # Get user
    user = get_or_create_user(telegram_id, None, "User")
    
    # Pop from navigation stack (returns tuple [screen, param])
    previous = pop_navigation_stack(user.id)
    
    if previous:
        previous_screen, previous_param = previous
        # Navigate to previous screen (don't add current to stack)
        return navigate_to(bot, telegram_id, previous_screen, param=previous_param, add_to_stack=False)
    else:
        # No history, go to hub
        return go_home(bot, telegram_id)


def go_home(bot, telegram_id):
    """
    Navigate to hub and clear navigation stack.
    """
    user = get_or_create_user(telegram_id, None, "User")
    
    # Clear navigation stack by updating with empty stack
    session = get_or_create_session(user.id)
    update_session_state(user.id, screen="SCR_HUB")
    session.navigation_stack = "[]"
    
    # Navigate to hub
    return navigate_to(bot, telegram_id, "SCR_HUB", add_to_stack=False)
