import os
import sys

# Ensure project root is in path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from database.models import SystemLock, User
from database.crud import SessionLocal
from config import ADMIN_IDS

def is_content_locked(telegram_id: int, action: str, screen: str, param: str) -> tuple[bool, str]:
    """
    Check if the requested content is locked.
    Returns (is_locked, reason_message)
    """
    # 1. Admin Screens don't get locked by content locks
    if screen and (screen.startswith("SCR_LOCK_") or screen.startswith("SCR_ADMIN_")):
        return False, ""

    # 2. Admins bypass locks
    # Note: We check locks for everyone. If locked and user is Admin, 
    # we return False (Allow) but with a specific reason string.

    db = SessionLocal()
    try:
        # --- FEATURE LOCKS ---
        feature_map = {
            # Advanced Practice Screens
            "SCR_GAMEMODE": "ADVANCED_PRACTICE",
            "SCR_SPEEDRUN_SETUP": "ADVANCED_PRACTICE",
            "SCR_SPEEDRUN_SUBJECTS": "ADVANCED_PRACTICE",
            "SCR_SPEEDRUN_COUNTS": "ADVANCED_PRACTICE",
            "SCR_SURVIVAL_SETUP": "ADVANCED_PRACTICE",
            "SCR_GAME_PRES": "ADVANCED_PRACTICE",
            
            # Review Hub
            "SCR_REVIEW_HUB": "REVIEW_HUB",
            
            # PDF Vault
            "SCR_PDF_VAULT": "PDFS_AND_FILES",
            
            # Leaderboard
            "SCR_RANKING": "LEADERBOARD",
            
            # Multiplayer
            "SCR_MULTIPLAYER_HUB": "PRACTICE_WITH_FRIENDS",
            "SCR_MP_SUBJ_SELECT": "PRACTICE_WITH_FRIENDS",
            "SCR_INVITES": "PRACTICE_WITH_FRIENDS",
            
            # AI Tutor (if screen exists, or general feature check)
            "SCR_AI_CHAT": "AI_TUTOR"
        }
        
        target_feature = feature_map.get(screen)
        
        # Also check ACT params that imply features
        if action == "ACT":
             if param and "SPEEDRUN" in param: target_feature = "ADVANCED_PRACTICE"
             if param and "SURVIVAL" in param: target_feature = "ADVANCED_PRACTICE"
             if param and "MP" in param: target_feature = "PRACTICE_WITH_FRIENDS"

        if target_feature:
            lock = db.query(SystemLock).filter(
                SystemLock.lock_type == "FEATURE", 
                SystemLock.lock_target == target_feature,
                SystemLock.is_locked == True
            ).first()
            if lock:
                msg = f"{target_feature.replace('_', ' ').title()} is currently locked."
                if telegram_id in ADMIN_IDS: return False, f"🛡️ LOCKED (Admin Bypass): {msg}"
                return True, f"🔒 {msg}"

        # --- GRADE LOCKS ---
        # Infer grade from param
        target_grade = None
        grade_str = None
        
        # Try to extract grade
        if param:
            # 1. Direct digit (e.g., "9")
            if str(param).isdigit() and int(param) in [9,10,11,12]:
                grade_str = str(param)
            # 2. Key-Value style or delimited (e.g., "BIO:9" or "START_RANDOM|10")
            else:
                # Try splitting by common delimiters
                parts = str(param).replace("|", ":").split(":")
                for p in parts:
                    clean_p = p.replace("Grade", "").replace("G", "").strip()
                    if clean_p.isdigit() and int(clean_p) in [9,10,11,12]:
                        grade_str = clean_p
                        break

        # --- GRADE INFERENCE FALLBACK ---
        # If no grade in param, checking content screens requires User's Grade
        if not grade_str:
             content_screens = [
                 "SCR_UNITS", "SCR_QUIZ_PRES", "SCR_TOPIC_SELECTION", "SCR_UNIT_START", 
                 "SCR_REVIEW_HUB", "SCR_SPEEDRUN_SUBJECTS", "SCR_SURVIVAL_SETUP", 
                 "SCR_PDF_VAULT", "SCR_RANDOM_SETUP"
             ]
             if screen in content_screens or (action == "ACT" and ("QUIZ" in param or "SPEEDRUN" in param or "SURVIVAL" in param)):
                 # Fetch user to get current grade
                 u_obj = db.query(User).filter(User.id == telegram_id).first()
                 if u_obj and u_obj.current_grade:
                     grade_str = str(u_obj.current_grade)

        # Check for Grade Lock (implicit or explicit)
        # Note: Grade-level locks are deprecated per user request. 
        # We restore the variable assignment for Subject Locks usage.
        
        if grade_str:
            target_grade = int(grade_str)

        # --- SUBJECT LOCKS ---
        subj_map = {"BIO": "Biology", "CHEM": "Chemistry", "PHYS": "Physics", "MATH": "Mathematics"}
        target_subject_code = None
        
        if param:
            upper_p = str(param).upper()
            for code in subj_map:
                if code in upper_p:
                   target_subject_code = code
                   break
        
        if target_subject_code:
            subj_name = subj_map[target_subject_code]
            
            # Check Grade-Specific lock
            if target_grade:
                specific_target = f"{subj_name}:{target_grade}"
                lock = db.query(SystemLock).filter(
                    SystemLock.lock_type == "SUBJECT", 
                    SystemLock.lock_target == specific_target,
                    SystemLock.is_locked == True
                ).first()
                if lock:
                    msg = f"{subj_name} for Grade {target_grade} is locked."
                    if telegram_id in ADMIN_IDS: return False, f"🛡️ LOCKED (Admin Bypass): {msg}"
                    return True, f"🔒 {msg}"
            
            # Global Subject Locks are deprecated in favor of Grade-Specific locks.
            # We skip the global check to avoid 'hidden' locks that can't be toggled in the new UI.

        # --- UNIT LOCKS ---
        potential_ids = []
        # Support detection in QUIZ, FILE, and GAME actions
        if screen in ["SCR_QUIZ_PRES", "SCR_PDF_VAULT", "SCR_GAME_PRES"] or (action == "ACT" and ("QUIZ" in param or "FILE" in param or "SPEEDRUN" in param)):
            if param:
               # Try to find unit ID pattern CODE_G#_U#
               import re
               matches = re.findall(r'([A-Z]+)_G([0-9]+)_U([0-9]+)', str(param))
               if matches:
                   for m in matches:
                       potential_ids.append(f"{m[0]}_G{m[1]}_U{m[2]}")
               
               # Fallback to delimited parsing
               if not potential_ids and (":" in str(param) or "|" in str(param)):
                  parts = str(param).replace("|", ":").split(":") 
                  try:
                      s_code = None
                      g_code = None
                      u_code = None
                      for p in parts:
                          p = p.strip()
                          if p in subj_map: s_code = p
                          elif "G" in p or "Grad" in p: 
                              g_code = p.replace("Grade", "").replace("G", "").strip()
                          elif "U" in p or "init" in p:
                              u_code = p.replace("Unit", "").replace("U", "").strip()
                      if s_code and g_code and u_code:
                          unit_id = f"{s_code}_G{g_code}_U{u_code}"
                          potential_ids.append(unit_id)
                  except: pass

        if potential_ids:
            locks = db.query(SystemLock).filter(
                SystemLock.lock_type == "UNIT",
                SystemLock.lock_target.in_(potential_ids),
                SystemLock.is_locked == True
            ).all()
            if locks:
                msg = f"This unit is currently locked."
                if telegram_id in ADMIN_IDS: return False, f"🛡️ LOCKED (Admin Bypass): {msg}"
                return True, f"🔒 {msg}"
                
        return False, ""
    except Exception as e:
        print(f"[LOCK ERROR] {e}")
        return False, ""
    finally:
        db.close()
