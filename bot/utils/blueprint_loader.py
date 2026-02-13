"""Blueprint loader utility - loads and caches the UI blueprint JSON"""
import json
import os

_blueprint_cache = None


def load_blueprint():
    """Load blueprint JSON file and cache it"""
    global _blueprint_cache
    if _blueprint_cache is None:
        # Navigate from bot/utils/ to project root
        blueprint_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "nebular_cassini_v1_blueprint.json"
        )
        with open(blueprint_path, 'r', encoding='utf-8') as f:
            _blueprint_cache = json.load(f)
    return _blueprint_cache


def get_screen(screen_id):
    """
    Get a specific screen definition from the blueprint.
    Searches by screen_id field value (e.g., "SCR_HUB"), not by key name.
    """
    blueprint = load_blueprint()
    screens = blueprint.get("screens", {})
    
    # Search for screen by screen_id field
    for key, screen_data in screens.items():
        if screen_data.get("screen_id") == screen_id:
            return screen_data
    
    return None


def reload_blueprint():
    """Force reload blueprint (useful for development)"""
    global _blueprint_cache
    _blueprint_cache = None
    return load_blueprint()
