"""
Screen renderer - converts blueprint screens to Telegram messages
"""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import json
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from utils.blueprint_loader import get_screen
from database.crud import get_or_create_user, get_review_queue_counts, get_all_user_progress, get_leaderboard, get_weekly_leaderboard
from database.db import SessionLocal
from database.models import User as UserModel, SystemLock
from utils.translations import TRANSLATIONS


def replace_variables(text, user_id, telegram_id, extra_vars=None, user_obj=None, progress_records=None):
    """
    Replace variables in text with actual values and handle translations.
    """
    
    # Get user from database if not provided
    if user_obj is None:
        user = get_or_create_user(telegram_id, None, "User")
    else:
        user = user_obj
        
    # Calculate global progress if not provided
    if progress_records is None:
        progress_records = get_all_user_progress(user.id)
    if progress_records is None: progress_records = []

    # Active Grade Logic
    active_grade = user.current_grade
    if extra_vars and "view_grade" in extra_vars:
        try: active_grade = int(str(extra_vars["view_grade"]))
        except: pass

    # Calculate mastery for ACTIVE grade
    active_progress = [p for p in progress_records if p.grade == active_grade]
    total_mastery = 0
    if active_progress:
        total_mastery = sum(p.completion_percent for p in active_progress) / len(active_progress)
    
    # Calculate Best Subject for ACTIVE grade
    subject_stats = {}
    for p in active_progress:
        subject_stats[p.subject] = subject_stats.get(p.subject, 0) + p.questions_correct
    best_sub = max(subject_stats, key=subject_stats.get) if subject_stats else "N/A"
    
    # Calculate Badges (Global)
    badges = []
    if user.level >= 5: badges.append("🥇 High Achiever")
    if user.streak_count >= 7: badges.append("🔥 7-Day Streak")
    global_mastery = sum(p.completion_percent for p in progress_records) / len(progress_records) if progress_records else 0
    if global_mastery >= 90: badges.append("🏆 Mastery Expert")
    if not badges: badges = ["No medals yet."]
    
    # Rank names
    ranks = {1: "Novice", 5: "Apprentice", 10: "Scholar", 20: "Sage", 50: "Master"}
    rank_name = "Novice"
    for lvl, name in sorted(ranks.items(), reverse=True):
        if user.level >= lvl:
            rank_name = name
            break

    def escape_md(val):
        if not val or not isinstance(val, str): return str(val)
        return val.replace("*", "\\*").replace("_", "\\_").replace("`", "\\`")

    def create_progress_bar(perc, length=10):
        """Creates a professional block-based progress bar."""
        filled = int((perc / 100) * length)
        if perc > 0 and filled == 0:
            filled = 1 # Show at least one block if started
        return "▰" * filled + "▱" * (length - filled)

    # Unit Breakdown & Subject Audit for SCR_STATS_DETAIL (Uses active_grade)
    unit_break_list = "No units practiced yet."
    subject_rank = "N/A"
    subject_coverage = "0/0"
    
    if extra_vars and "subject" in extra_vars:
        sub_code = extra_vars["subject"]
        sub_full = {"BIO": "Biology", "CHEM": "Chemistry", "PHYS": "Physics", "MATH": "Mathematics"}.get(sub_code, sub_code)
        
        # 1. Fetch ALL units for the ACTIVE Grade & Subject
        from utils.question_engine import QuestionEngine
        all_units = QuestionEngine.list_units(sub_full, f"Grade {active_grade}")
        
        # Robust helper to extract unit number from various ID formats
        def get_unit_num(u_string):
             # Handle "BIO_G9_U1"
             if "_" in u_string and "U" in u_string.split("_")[-1]:
                 return u_string.split("_")[-1].replace("U", "")
             # Handle "Unit 1: Intro" or "Unit 1"
             if "Unit " in u_string:
                 try:
                     parts = u_string.split("Unit ")[1] # "1: Intro"
                     return parts.split(":")[0].strip() # "1"
                 except: pass
             # Fallback: assume just number string if digit
             return u_string

        # 2. Match progress (Filter by active_grade)
        relevant_dict = {get_unit_num(p.unit_id): p for p in active_progress if p.subject == sub_full}
        
        if all_units:
            count_mastered = 0
            lines = [f"📊 *Mastery Breakdown: {sub_full} (Grade {active_grade})*"]
            for i, u in enumerate(all_units):
                u_num = str(i + 1)
                # Try explicit match first, then title match
                p = relevant_dict.get(u_num)
                
                # Fallback: check if unit title matches key if mapped poorly
                if not p:
                    # Sometimes unit_id IS the title "Unit 1: Intro"
                    p = next((p for p in active_progress if p.subject == sub_full and p.unit_id == u), None)
                
                u_perc = int(p.completion_percent) if p else 0
                u_bar = create_progress_bar(u_perc, length=8)
                phase_label = p.current_phase.replace("_BIASED", "").title() if p else "Not Started"
                if u_perc >= 80: count_mastered += 1
                lines.append(f"Unit {u_num}: {u_bar} {u_perc}% ({phase_label})")
            
            unit_break_list = "\n".join(lines)
            subject_coverage = f"{count_mastered}/{len(all_units)} Units Mastered"
            
            # 3. Calculate Subject Rank (Global comparison in this sub)
            from database.models import Progress as ProgressModel
            from sqlalchemy import func
            db = SessionLocal()
            ranks_query = db.query(
                ProgressModel.user_id, 
                func.sum(ProgressModel.questions_correct).label('total_sub_correct')
            ).filter(ProgressModel.subject == sub_full).group_by(ProgressModel.user_id).order_by(func.sum(ProgressModel.questions_correct).desc()).all()
            
            for i, (uid, _) in enumerate(ranks_query):
                if uid == user.id:
                    subject_rank = f"#{i+1}"
                    break
            db.close()

    # Level Progress
    next_level_xp = ((user.level) ** 2) * 100
    prev_level_xp = ((user.level - 1) ** 2) * 100
    total_needed = next_level_xp - prev_level_xp
    current_in_level = user.total_xp - prev_level_xp
    level_perc = int((current_in_level / total_needed) * 100) if total_needed > 0 else 100
    level_bar = create_progress_bar(max(0, min(100, level_perc)), length=12)
            
    # Dynamic Greetings & Tips
    import random
    from datetime import datetime
    hour = datetime.utcnow().hour + 3 # Approximate EAT
    if hour < 12: greeting = "Good Morning! ☀️"
    elif hour < 18: greeting = "Good Afternoon! 🌤️"
    else: greeting = "Good Evening! 🌙"
    
    tips = [
        "Consistency is key! Regular practice leads to better results.",
        "Review your mistakes to strengthen your understanding.",
        "Collaborate with a friend to stay motivated!",
        "Try Timed Practice to build your exam-day speed.",
        "Check the Leaderboard to see your academic standing!",
        "Master the difficult units to boost your knowledge level.",
        "Academic mastery takes time. Keep practicing!",
        "Biology requires memorization; focus on key terms!",
        "Physics is about understanding concepts, not just formulas.",
        "Chemistry reactions follow logical patterns. Study the trends!",
        "Practice regularly to improve your recall and problem-solving skills.",
        "Don't be afraid to ask for help when you're stuck on a concept.",
        "Break down complex topics into smaller, manageable parts.",
        "Set clear academic goals to stay focused and motivated.",
        "Review past questions to identify areas for improvement."
    ]
    random_tip = random.choice(tips)

    replacements = {
        "{user_name}": escape_md(user.full_name),
        "{greeting}": greeting,
        "{random_tip}": random_tip,
        "{telegram_id}": str(telegram_id),
        "{level}": str(user.level),
        "{level_rank}": escape_md(f"{rank_name} (Lvl {user.level})"), 
        "{rank}": escape_md(rank_name),
        "{streak}": str(user.streak_count),
        "{streak_count}": str(user.streak_count),
        "{mastery}": f"{total_mastery:.1f}",
        "{xp}": str(user.total_xp),
        "{view_grade}": str(active_grade),
        "{current_grade}": str(user.current_grade),
        "{grade}": str(user.current_grade),
        "{best_subject}": escape_md(best_sub),
        "{subject_rank}": subject_rank,
        "{subject_coverage}": subject_coverage,
        "{join_date}": user.join_date.strftime("%Y-%m-%d") if user.join_date else "Unknown",
        "{subject_name}": escape_md(extra_vars.get("subject_name", extra_vars.get("subject", "Subject")) if extra_vars else "Subject"),
        "{subject}": escape_md(extra_vars.get("subject", extra_vars.get("subject_name", "Subject")) if extra_vars else "Subject"),
        "{current_lang}": "English" if user.language == "EN" else "Amharic",
        "{notif_status}": "ON" if user.notifications_enabled else "OFF",
        "{badge_list}": "\n".join([f"🏅 {b}" for b in badges]),
        "{unit_breakdown_list}": unit_break_list,
        "{level_progress}": f"{level_bar} {level_perc}%",
        "{countdown}": "6d 12h",
        "{current_scope}": "Global",
        "{user_xp}": str(user.total_xp),
        "{needed_xp}": str(next_level_xp - user.total_xp if user.total_xp < next_level_xp else 100),
        "{param}": str(extra_vars.get("param", "")) if extra_vars else "",
        "{dur}": str(extra_vars.get("dur", "30")),
        "{cnt}": str(extra_vars.get("cnt", "30")),
        "{subj}": str(extra_vars.get("subj", "MIXED")),
    }

    # Leaderboard Logic - Support for Global and Weekly scopes
    leaderboard_scope = extra_vars.get("leaderboard_scope", "Global") if extra_vars else "Global"
    
    if leaderboard_scope == "Weekly":
        top_users = get_weekly_leaderboard(10)
        xp_field = "weekly_xp"
        
        # Calculate countdown to next Monday 00:00 UTC
        from datetime import datetime, timedelta
        now = datetime.utcnow()
        days_until_monday = (7 - now.weekday()) % 7
        if days_until_monday == 0 and now.hour == 0 and now.minute < 5:
            days_until_monday = 7  # If it's Monday morning, show 7 days
        next_monday = now + timedelta(days=days_until_monday)
        next_monday = next_monday.replace(hour=0, minute=0, second=0, microsecond=0)
        time_diff = next_monday - now
        days = time_diff.days
        hours = time_diff.seconds // 3600
        countdown = f"{days}d {hours}h"
    else:
        top_users = get_leaderboard(10)
        xp_field = "total_xp"
        countdown = "Never"  # Global leaderboard doesn't reset
    
    lines = []
    user_rank = "100+"
    
    db = SessionLocal()
    # Find active user rank efficiently using COUNT
    if leaderboard_scope == "Weekly":
        user_rank_num = db.query(UserModel).filter(UserModel.weekly_xp > user.weekly_xp).count() + 1
        user_xp_display = user.weekly_xp
    else:
        user_rank_num = db.query(UserModel).filter(UserModel.total_xp > user.total_xp).count() + 1
        user_xp_display = user.total_xp
    
    user_rank = str(user_rank_num)
    db.close()

    for i, u in enumerate(top_users):
        medal = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else "👤"
        name = u.full_name if u.id != user.id else f"*{u.full_name} (You)*"
        xp_value = u.weekly_xp if leaderboard_scope == "Weekly" else u.total_xp
        lines.append(f"{medal} {name} - {xp_value} XP")
    
    replacements["{rank_list}"] = "\n".join(lines)
    replacements["{user_rank}"] = user_rank
    replacements["{countdown}"] = countdown
    replacements["{current_scope}"] = leaderboard_scope
    replacements["{user_xp}"] = str(user_xp_display)

    # Admin Stats
    from config import ADMIN_IDS
    if telegram_id in ADMIN_IDS:
        db_admin = SessionLocal()
        from sqlalchemy import func
        from database.models import Progress as ProgressModel, FlaggedQuestion
        
        replacements["{total_users}"] = str(db_admin.query(UserModel).count())
        replacements["{total_xp_global}"] = str(db_admin.query(func.sum(UserModel.total_xp)).scalar() or 0)
        
        total_attempts = db_admin.query(func.sum(ProgressModel.questions_attempted)).scalar() or 0
        total_correct = db_admin.query(func.sum(ProgressModel.questions_correct)).scalar() or 0
        
        replacements["{total_attempts}"] = str(total_attempts)
        replacements["{total_mistakes}"] = str(total_attempts - total_correct)
        replacements["{total_flagged}"] = str(db_admin.query(FlaggedQuestion).count())
        db_admin.close()
    
    # Subject progress bars (for STATS screen)
    for sub in ["bio", "chem", "phys", "math"]:
        sub_full = {"bio": "Biology", "chem": "Chemistry", "phys": "Physics", "math": "Mathematics"}[sub]
        relevant = [p for p in active_progress if p.subject == sub_full]
        perc = sum(p.completion_percent for p in relevant) / len(relevant) if relevant else 0
        p_bar = create_progress_bar(perc, length=10)
        replacements[f"{{{sub}_dots}}"] = p_bar
        replacements[f"{{{sub}_perc}}"] = str(int(perc))
        replacements[f"{{{sub}_badges}}"] = "🎖️" if perc > 80 else ""
    
    if extra_vars:
        for k, v in extra_vars.items():
            if k in ["ai_explanation", "question_stem"]: 
                replacements[f"{{{k}}}"] = v
            else:
                # [FIX]: Allow extra_vars to overwrite defaults (like {grade})
                replacements[f"{{{k}}}"] = escape_md(v)
    
    # Logic for Grade Button Highlighting (Premium UI)
    if text.startswith("🎓 G"):
        try:
            btn_grade = text.replace("🎓 G", "")
            if str(btn_grade) == str(active_grade):
                text = text.replace("🎓", "✅")
        except:
            pass

    # Speedrun Configuration Highlighting
    if extra_vars:
        if " min" in text and "dur" in extra_vars:
            try:
                val = text.replace("🕒 ", "").replace(" min", "").strip()
                if str(val) == str(extra_vars["dur"]):
                    text = text.replace("🕒", "✅")
            except: pass
        if " Qs" in text and "cnt" in extra_vars:
            try:
                val = text.replace("🔢 ", "").replace(" Qs", "").strip()
                if str(val) == str(extra_vars["cnt"]):
                    text = text.replace("🔢", "✅")
            except: pass

    result = text
    for key, value in replacements.items():
        result = result.replace(key, str(value))
    
    return result


def build_keyboard(layout, actions, user_id, telegram_id, extra_vars=None, user_obj=None, progress_records=None):
    """
    Build InlineKeyboardMarkup from layout and actions.
    """
    keyboard = []
    
    for row in layout:
        button_row = []
        for label in row:
            callback_data = actions.get(label, "NOOP")
            # Handle dynamic parameters in callback data
            if extra_vars:
                # Generic replacement for all variables in extra_vars
                for k, v in extra_vars.items():
                    placeholder = f"{{{k}}}"
                    if placeholder in str(callback_data):
                        callback_data = callback_data.replace(placeholder, str(v))
                
                # Historic/Specific handlers (legacy support or specific overrides) can remain if needed
                if "param" in extra_vars and "{param}" in str(callback_data): # Redundant now but safe
                    callback_data = callback_data.replace("{param}", str(extra_vars["param"]))
                
                # FORCE update for Random Quiz to verify it's working
                if "START_RANDOM_QUIZ" in str(callback_data) or "ACT|QUIZ|RANDOM" in str(callback_data):
                    if "view_grade" in extra_vars:
                         callback_data = callback_data.replace("{view_grade}", str(extra_vars["view_grade"]))
                
            display_label = replace_variables(label, user_id, telegram_id, extra_vars, user_obj=user_obj, progress_records=progress_records)
            button_row.append(InlineKeyboardButton(display_label, callback_data=callback_data))
        keyboard.append(button_row)
    
    return InlineKeyboardMarkup(keyboard)


def render_screen(bot, user_id, telegram_id, screen_id, message_id=None, extra_vars=None):
    """
    Render a screen from the blueprint with translations and dynamic logic.
    """
    from utils.question_engine import QuestionEngine
    import json
    from utils.blueprint_loader import get_screen, reload_blueprint
    
    # FORCE reload for Random Setup screen to ensure new actions are picked up
    # Also for new Resources screens during dev
    if screen_id in ["SCR_RANDOM_SETUP", "SCR_RESOURCES_HUB", "SCR_RESOURCES_ACTIONS"]:
        reload_blueprint()
        
    user_obj = get_or_create_user(telegram_id, None, "User")
    lang = user_obj.language or "EN"
    
    raw_screen = get_screen(screen_id)
    if not raw_screen:
        error_text = f"[ERROR] Screen '{screen_id}' not found"
        return bot.send_message(chat_id=telegram_id, text=error_text) if not message_id else bot.edit_message_text(chat_id=telegram_id, message_id=message_id, text=error_text)
    
    # Data copy for security
    screen = raw_screen.copy()
    if extra_vars is None: extra_vars = {}

    subj_map = {"BIO": "Biology", "CHEM": "Chemistry", "PHYS": "Physics", "MATH": "Mathematics"}
    

    # --- STATS & PROGRESS SCREENS ---
    # Global default for view_grade
    if "view_grade" not in extra_vars:
        extra_vars["view_grade"] = str(user_obj.current_grade)

    if screen_id == "SCR_GRADES":
        code = extra_vars.get("param")
        if code:
            extra_vars["subject"] = code
            extra_vars["subject_name"] = subj_map.get(code, code)

    if screen_id in ["SCR_STATS", "SCR_GAMEMODE", "SCR_SUBJECTS", "SCR_RANDOM_SETUP", "SCR_RESOURCES_HUB", "SCR_LOCK_GRADES", "SCR_LOCK_SUBJECTS", "SCR_LOCK_UNITS"]:
        grade_param = extra_vars.get("param")
        if grade_param and (str(grade_param).isdigit() or str(grade_param) == "ROOT"):
            if str(grade_param).isdigit():
                extra_vars["view_grade"] = str(grade_param)
            # If ROOT, we rely on the default set above (user_obj.current_grade) if not present
            
            print(f"[RENDER] {screen_id} view_grade updated to {extra_vars.get('view_grade')}")
        elif screen_id == "SCR_RANDOM_SETUP":
             if not extra_vars.get("view_grade"):
                 extra_vars["view_grade"] = str(user_obj.current_grade)
             print(f"[RENDER] {screen_id} Final view_grade: {extra_vars['view_grade']}")

    if screen_id == "SCR_RESOURCES_ACTIONS":
        param = extra_vars.get("param", "")
        if ":" in str(param):
            parts = str(param).split(":")
            sub_code = parts[0]
            extra_vars["subject"] = sub_code
            extra_vars["view_grade"] = parts[1]
            extra_vars["subject_name"] = subj_map.get(sub_code, sub_code)

    if screen_id == "SCR_STATS_DETAIL":
        param = extra_vars.get("param", "")
        if ":" in str(param):
            parts = str(param).split(":")
            sub_code = parts[0]
            extra_vars["subject"] = sub_code
            extra_vars["view_grade"] = parts[1]
            extra_vars["subject_name"] = subj_map.get(sub_code, sub_code)
        else:
            sub_code = param
            if sub_code:
                extra_vars["subject"] = sub_code
                extra_vars["subject_name"] = subj_map.get(sub_code, sub_code)

    # Chaining view_grade through setup screens
    if screen_id in ["SCR_SPEEDRUN_HUB", "SCR_SURVIVAL_SETUP"]:
        param = extra_vars.get("param", "")
        # If param is a grade (e.g. "9")
        if str(param) and str(param).isdigit():
            extra_vars["view_grade"] = str(param)
        
        if screen_id == "SCR_SPEEDRUN_HUB":
            from database.crud import get_or_create_session
            session = get_or_create_session(user_obj.id)
            if session.quiz_state:
                try:
                    setup = json.loads(session.quiz_state)
                    # Only use if it looks like a setup dict, not an active game
                    if isinstance(setup, dict) and "dur" in setup and "mode" not in setup:
                        for k,v in setup.items():
                            if k not in extra_vars: extra_vars[k] = v
                except: pass
            # Fallbacks
            if "dur" not in extra_vars: extra_vars["dur"] = 30
            if "cnt" not in extra_vars: extra_vars["cnt"] = 30

    # SCR_SETTINGS
    if screen_id == "SCR_SETTINGS":
        extra_vars["current_lang"] = "English" if user_obj.language == "EN" else "Amharic"
        extra_vars["notif_status"] = "ON" if user_obj.notifications_enabled else "OFF"
    layout = screen.get("layout", [])
    if not isinstance(layout, list): layout = screen.get("buttons", [])
    actions = screen.get("actions", {}).copy()

    # --- Dynamic Content Generations ---
    
    # --- PDF VAULT SCREEN ---
    if screen_id == "SCR_PDF_VAULT":
        # Parse param: "BIO:Grade 12" or "BIO:12"
        sub_code = None
        grade_str = None
        
        param = extra_vars.get("param")
        if param and ":" in param:
            parts = param.split(":")
            sub_code = parts[0]
            grade_str = parts[1]
            # Normalize grade format
            if "Grade" not in grade_str:
                grade_str = f"Grade {grade_str}"
        
        if sub_code and grade_str:
            subject_name = subj_map.get(sub_code, sub_code)
            
            # Load available units
            units = QuestionEngine.list_units(subject_name, grade_str)
            
            if units:
                # Build header
                screen["header_text"] = f"📂 *Study Library: {subject_name} ({grade_str})*\n\nAccess unit-based study guides with curated MCQs and detailed explanations for offline review."
                
                # Build unit buttons (2 per row for better layout)
                unit_rows = []
                for i in range(0, len(units), 2):
                    row = []
                    for j in range(2):
                        if i + j < len(units):
                            unit = units[i + j]
                            label = f"📄 {unit}"
                            actions[label] = f"ACT|PDF|DOWNLOAD_UNIT|{sub_code}:{grade_str}:{unit}"
                            row.append(label)
                    unit_rows.append(row)
                
                # Add "Download All Units" button
                all_label = f"📚 Download Comprehensive Guide ({len(units)} units)"
                actions[all_label] = f"ACT|PDF|DOWNLOAD_ALL|{sub_code}:{grade_str}"
                
                # Build final layout
                layout = unit_rows + [[all_label], ["🔙 Back", "🏠 Home"]]
                actions["🔙 Back"] = "NAV|BACK|BACK"
                actions["🏠 Home"] = "NAV|SCR_HUB|ROOT"
            else:
                # No units found
                screen["header_text"] = f"📂 *Download Lesson PDFs*\\n\\n{subject_name} - {grade_str}\\n\\n❌ No units available for this subject and grade."
                layout = [["🔙 Back", "🏠 Home"]]
                actions["🔙 Back"] = "NAV|BACK|BACK"
                actions["🏠 Home"] = "NAV|SCR_HUB|ROOT"
        else:
            # Invalid param
            screen["header_text"] = "📂 *Download Lesson PDFs*\\n\\n❌ Invalid subject or grade selection."
            layout = [["🏠 Home"]]
            actions["🏠 Home"] = "NAV|SCR_HUB|ROOT"
    
    # SCR_GRADES
    if screen_id == "SCR_GRADES":
        code = extra_vars.get("subject", extra_vars.get("param", ""))
        subject = subj_map.get(code, code)
        if subject:
            grades = QuestionEngine.list_grades(subject)
            if grades:
                grid = []
                for i, g in enumerate(grades):
                    actions[g] = f"NAV|SCR_UNITS|{code}:{g}"
                    if i % 2 == 0: grid.append([g])
                    else: grid[-1].append(g)
                layout = grid + screen.get("bottom_rows", [["🔙 Back to Subjects", "🏠 Home"]])

    # SCR_UNITS
    if screen_id == "SCR_UNITS":
        context = extra_vars.get("param")
        if context and ":" in context:
            code, grade_raw = context.split(":")
            # Normalize to "Grade X" for the engine
            grade_full = f"Grade {grade_raw}" if grade_raw.isdigit() else grade_raw
            
            extra_vars["subject_code"] = code
            extra_vars["subject_name"] = subj_map.get(code, code)
            extra_vars["grade"] = grade_full.replace("Grade ", "")
            
            # Fetch LOCKS for this subject/grade
            from database.models import SystemLock
            db = SessionLocal()
            grade_val = grade_raw.replace("Grade ", "").strip()
            unit_locks = db.query(SystemLock.lock_target).filter(
                SystemLock.lock_type == "UNIT",
                SystemLock.lock_target.like(f"{code}_G{grade_val}_U%"),
                SystemLock.is_locked == True
            ).all()
            locked_unit_ids = [l[0] for l in unit_locks]
            db.close()

            units = QuestionEngine.list_units(extra_vars["subject_name"], grade_full)
            grid = []
            if units:
                all_p = get_all_user_progress(user_obj.id)
                for i, u in enumerate(units):
                    unit_num = u.split(" ")[1] if " " in u else str(i+1)
                    unit_id = f"{code}_{grade_full.replace(' ', '')}_U{unit_num}"
                    this_p = next((p for p in all_p if p.unit_id == unit_id), None)
                    perc = int(this_p.completion_percent) if this_p else 0
                    
                    is_locked = unit_id in locked_unit_ids
                    
                    if is_locked:
                        label = f"{u} 🔒"
                        actions[label] = "ACT|QUIZ|UNIT_LOCKED"
                    else:
                        label = f"{u} ✅" if perc == 100 else (f"{u} {perc}%" if perc > 0 else u)
                        actions[label] = f"NAV|SCR_QUIZ_PRES|{code}:{grade_full}:{u}"
                    
                    if i % 2 == 0: grid.append([label])
                    else: grid[-1].append(label)
            
            # Ensure bottom_rows are always added
            bottom_rows = screen.get("bottom_rows", [["⚡ Review All Units"], ["📂 Unit Study Guides"], ["🔙 Back to Grades", "🏠 Home"]])
            layout = grid + bottom_rows
            
            # Map bottom row actions for dynamic context
            for row in bottom_rows:
                for lbl in row:
                    if lbl == "⚡ Review All Units":
                        actions[lbl] = f"NAV|SCR_REVIEW_HUB|{code}:{grade_full}"
                    if lbl == "📂 Unit Study Guides":
                        actions[lbl] = f"NAV|SCR_PDF_VAULT|{code}:{grade_full}"
                    if lbl == "🔙 Back to Grades":
                        actions[lbl] = f"NAV|SCR_SUBJECTS|{grade_raw}"

    # Explicitly fix actions for Random Setup
    if screen_id == "SCR_RANDOM_SETUP":
         vg = extra_vars.get("view_grade", str(user_obj.current_grade))
         # We need to overwrite the action for the specific button label using the CURRENT view_grade
         # The label in blueprint is "⚡ Start Random Quiz"
         actions["⚡ Start Random Quiz"] = f"ACT|QUIZ|START_RANDOM_QUIZ|{vg}"
         
         # Force update any potential old keys just in case
         actions[f"ACT|QUIZ|START_RANDOM_QUIZ|{{view_grade}}"] = f"ACT|QUIZ|START_RANDOM_QUIZ|{vg}"
         
         print(f"[RENDER] Forced Random Quiz Action: {actions.get('⚡ Start Random Quiz')}")
         # Also ensure grade buttons highlight
         if f"🎓 G{vg}" in actions:
             # Need to find the key that matches "🎓 G{vg}"
             pass # Logic below handles text replacement but let's highlight action if needed
             
    # SCR_REVIEW_HUB, SCR_QUIZ_SUM, SCR_REVIEW_SUM
    if screen_id in ["SCR_REVIEW_HUB", "SCR_QUIZ_SUM", "SCR_REVIEW_SUM"]:
        sub_code = None
        grade_num = None
        
        # 1. Prioritize extra_vars['param'] for subject and grade
        param = extra_vars.get("param")
        if param and ":" in param:
            parts = param.split(":")
            sub_code = parts[0]
            grade_str = parts[1]
            try:
                # Extract grade number, handling "Grade X" or just "X"
                grade_num = int(grade_str.replace("Grade ", ""))
            except ValueError:
                grade_num = None # Fallback if parsing fails
        
        # 2. Fallback to session if param not fully available or invalid
        if not sub_code or grade_num is None:
            from database.crud import get_or_create_session
            session = get_or_create_session(user_obj.id)
            if session.quiz_state:
                import json
                qs = json.loads(session.quiz_state)
                if not sub_code: sub_code = qs.get("subject_code")
                if grade_num is None:
                    g_val = qs.get("grade")
                    if g_val:
                        try:
                            grade_num = int(str(g_val).replace("Grade ", ""))
                        except ValueError:
                            grade_num = None
        
        # Default to 9 if grade_num is still not set (e.g., for general review)
        if grade_num is None:
            grade_num = 9 # A reasonable default if no context is found

        if sub_code:
            extra_vars["subject_name"] = subj_map.get(sub_code, sub_code)
            # Pass real grade integer to get_review_queue_counts
            counts = get_review_queue_counts(user_obj.id, subject=sub_code, grade=grade_num)
            
            m_count = counts.get("MISTAKE", 0)
            s_count = counts.get("SKIPPED", 0)
            
            p_count = counts.get("PINNED", 0)
            
            review_rows = []
            if m_count > 0:
                label = f"❌ Retry Mistakes {m_count}"
                actions[label] = "ACT|QUIZ|REVIEW_MISTAKES"
                review_rows.append([label]) # Vertical stack for small screens
                extra_vars["mistake_count"] = m_count
            if s_count > 0:
                label = f"⏩ Try Skipped {s_count}"
                actions[label] = "ACT|QUIZ|REVIEW_SKIPPED"
                review_rows.append([label]) # Vertical stack for small screens
                extra_vars["skipped_count"] = s_count
            if p_count > 0:
                label = f"📌 Pinned Questions {p_count}"
                actions[label] = "ACT|QUIZ|REVIEW_PINNED"
                review_rows.append([label])
                extra_vars["pinned_count"] = p_count
            
            if review_rows:
                # Vertical stacking prevents overflow on small platforms
                layout = review_rows + layout

    # SCR_PDF_VAULT
    if screen_id == "SCR_PDF_VAULT":
        context = extra_vars.get("param")
        if context and ":" in context:
            code, grade = context.split(":")
            extra_vars["subject_name"] = subj_map.get(code, code)
            extra_vars["grade"] = grade.replace("Grade ", "")
            
            # Fetch LOCKS for this subject/grade
            from database.models import SystemLock
            db = SessionLocal()
            grade_val = grade.replace("Grade ", "").strip()
            # Find all UNIT locks for this subject/grade
            unit_locks = db.query(SystemLock).filter(
                SystemLock.lock_type == "UNIT",
                SystemLock.lock_target.like(f"{code}_G{grade_val}_U%"),
                SystemLock.is_locked == True
            ).all()
            locked_unit_ids = [l.lock_target for l in unit_locks]
            db.close()

            units = QuestionEngine.list_units(extra_vars["subject_name"], grade)
            if units:
                grid = []
                # Add "All Volume" button at top
                all_label = "📚 DOWNLOAD ALL UNITS"
                actions[all_label] = f"ACT|FILE|SEND_PDF_ALL|{code}:{grade}"
                grid.append([all_label])
                
                for i, u in enumerate(units):
                    u_num = u.split(" ")[1] if " " in u else str(i+1)
                    unit_id = f"{code}_G{grade_val}_U{u_num}"
                    
                    is_locked = unit_id in locked_unit_ids
                    status_icon = "🔒" if is_locked else "📥"
                    
                    label = f"{status_icon} Download {u}"
                    if is_locked:
                        actions[label] = "ACT|QUIZ|UNIT_LOCKED"
                    else:
                        actions[label] = f"ACT|FILE|SEND_PDF_UNIT|{code}:{grade}:{u}"
                        
                    if i % 2 == 0: grid.append([label])
                    else: grid[-1].append(label)
                
                layout = grid + screen.get("bottom_rows", [["🔙 Back to Units", "🏠 Home"]])

    # SCR_INVITES - Show user's created challenges
    if screen_id == "SCR_INVITES":
        from database.models import Challenge as ChallengeModel
        import json
        db = SessionLocal()
        try:
            user_obj = db.query(UserModel).filter(UserModel.telegram_id == telegram_id).first()
            if user_obj:
                challenges = db.query(ChallengeModel).filter(
                    ChallengeModel.creator_id == user_obj.id
                ).order_by(ChallengeModel.created_at.desc()).limit(10).all()
            else:
                challenges = []
        except Exception as e:
            print(f"[INVITES] Error loading challenges: {e}")
            challenges = []
        finally:
            db.close()
        
        if challenges:
            grid = []
            for ch in challenges:
                subj_name = ch.subject or "Mixed"
                q_count = len(json.loads(ch.questions_json)) if ch.questions_json else 0
                created = ch.created_at.strftime("%m/%d %H:%M") if ch.created_at else ""
                label = f"⚔️ {subj_name} G{ch.grade} ({q_count}Qs) {created}"
                # Truncate if too long for Telegram button
                if len(label) > 60:
                    label = label[:58] + ".."
                actions[label] = f"ACT|MP|SHARE_TRIGGER"
                grid.append([label])
            
            layout = grid + [["🔙 Back"]]
            screen["header_text"] = f"📥 *Your Active Challenges*\n━━━━━━━━━━━━━━\n📊 {len(challenges)} challenge(s) created\n\nTap any challenge to share its link with friends!"
        else:
            layout = [["⚔️ Create New Challenge"], ["🔙 Back"]]
            actions["⚔️ Create New Challenge"] = "NAV|SCR_MP_SUBJ_SELECT|ROOT"
            screen["header_text"] = "📥 *Your Active Challenges*\n━━━━━━━━━━━━━━\n\n📭 You have no active challenges.\n\nCreate one and share with friends! 🚀"
        
        actions["🔙 Back"] = "NAV|SCR_MULTIPLAYER_HUB|BACK"

    # SCR_ADMIN_FLAGS
    if screen_id == "SCR_ADMIN_FLAGS":
        from database.models import FlaggedQuestion
        db = SessionLocal()
        flags = db.query(FlaggedQuestion).order_by(FlaggedQuestion.flag_count.desc()).all()
        db.close()
        
        if flags:
            grid = []
            for f in flags:
                label = f"🚩 {f.question_id} ({f.flag_count})"
                actions[label] = f"NAV|SCR_ADMIN_FLAG_REVIEW|{f.question_id}"
                grid.append([label])
            layout = grid + [["🔙 Back to Admin"]]
        else:
            layout = [["🔙 Back to Admin"]]
            screen["header_text"] += "\n\n✨ *No flags found.* Everything is clean!"

    # SCR_ADMIN_FLAG_REVIEW
    if screen_id == "SCR_ADMIN_FLAG_REVIEW":
        q_id = extra_vars.get("param")
        if q_id:
            from database.models import FlaggedQuestion
            db = SessionLocal()
            f = db.query(FlaggedQuestion).filter(FlaggedQuestion.question_id == q_id).first()
            db.close()
            
            if f:
                q_data = QuestionEngine.find_question_by_id(q_id)
                import json
                reasons = json.loads(f.reasons)
                extra_vars.update({
                    "q_id": q_id,
                    "flag_count": f.flag_count,
                    "reasons": "\n".join([f"• {r}" for r in set(reasons)]),
                    "stem": q_data.get("question", "N/A") if q_data else "Question not found in JSON!",
                    "correct": q_data.get("correct_answer", "N/A") if q_data else "N/A"
                })

    # --- LOCK REGISTRY IMPLEMENTATION ---
    
    # SCR_ADMIN_LOCKS (Main Hub)
    if screen_id == "SCR_ADMIN_LOCKS":
        # Create explicit menu without "Lock Grades"
        layout = [
            ["🎮 Feature Locks"],
            ["📚 Subject Locks"],
            ["📖 Unit Locks"],
            ["🔙 Back to Admin"]
        ]
        actions["🎮 Feature Locks"] = "NAV|SCR_LOCK_FEATURES|ROOT"
        actions["📚 Subject Locks"] = "NAV|SCR_LOCK_SUBJECTS|ROOT"
        actions["📖 Unit Locks"] = "NAV|SCR_LOCK_UNITS|ROOT"
        actions["🔙 Back to Admin"] = "NAV|SCR_ADMIN|ROOT"
        
        screen["header_text"] = "🔐 *Admin Lock Registry*\n\nManage curriculum access and feature restrictions."

    # SCR_LOCK_FEATURES
    if screen_id == "SCR_LOCK_FEATURES":
        from database.models import SystemLock
        db = SessionLocal()
        
        features = ["ADVANCED_PRACTICE", "REVIEW_HUB", "PDFS_AND_FILES", "LEADERBOARD", "PRACTICE_WITH_FRIENDS", "SEARCH"]
        feature_locks = db.query(SystemLock).filter(
            SystemLock.lock_type == "FEATURE",
            SystemLock.lock_target.in_(features)
        ).all()
        lock_map = {l.lock_target: l.is_locked for l in feature_locks}
        db.close()
        
        grid = []
        for f in features:
            is_locked = lock_map.get(f, False)
            status = "🔴" if is_locked else "🔵"
            label = f"{f.replace('_', ' ').title()} {status}"
            actions[label] = f"ACT|LOCK|TOGGLE_FEATURE|{f}"
            grid.append([label])
            
        layout = grid + [["🔙 Back to Lock Registry"]]
        actions["🔙 Back to Lock Registry"] = "NAV|SCR_ADMIN_LOCKS|ROOT"

    # SCR_LOCK_GRADES
    if screen_id == "SCR_LOCK_GRADES":
        pass # Feature removed per request

    # SCR_LOCK_SUBJECTS
    if screen_id == "SCR_LOCK_SUBJECTS":
        view_grade = extra_vars.get("view_grade")
        if not view_grade: view_grade = "9"
        
        tabs = ["9", "10", "11", "12"]
        tab_row = []
        for g in tabs:
            lbl = f"🎓 G{g}" if str(g) != str(view_grade) else f"✅ G{g}"
            actions[lbl] = f"NAV|SCR_LOCK_SUBJECTS|{g}"
            tab_row.append(lbl)
            
        from database.models import SystemLock
        db = SessionLocal()
        
        subjects = ["Biology", "Chemistry", "Physics", "Mathematics"]
        # Targets: "Biology:9"
        targets = [f"{s}:{view_grade}" for s in subjects]
        sub_locks = db.query(SystemLock).filter(
            SystemLock.lock_type == "SUBJECT",
            SystemLock.lock_target.in_(targets)
        ).all()
        lock_map = {l.lock_target: l.is_locked for l in sub_locks}
        db.close()
        
        grid = []
        for s in subjects:
            target = f"{s}:{view_grade}"
            is_locked = lock_map.get(target, False)
            status = "🔴" if is_locked else "🔵"
            label = f"{s} {status}"
            actions[label] = f"ACT|LOCK|TOGGLE_SUBJECT|{target}"
            
            if len(grid) == 0 or len(grid[-1]) == 2:
                grid.append([label])
            else:
                grid[-1].append(label)
            
        layout = [tab_row] + grid + [["🔙 Back to Lock Registry"]]
        actions["🔙 Back to Lock Registry"] = "NAV|SCR_ADMIN_LOCKS|ROOT"

    # SCR_LOCK_UNITS (Subject Select with Grade Tabs)
    if screen_id == "SCR_LOCK_UNITS":
        view_grade = extra_vars.get("view_grade")
        if not view_grade: view_grade = "9"
        
        tabs = ["9", "10", "11", "12"]
        tab_row = []
        for g in tabs:
            lbl = f"🎓 G{g}" if str(g) != str(view_grade) else f"✅ G{g}"
            actions[lbl] = f"NAV|SCR_LOCK_UNITS|{g}"
            tab_row.append(lbl)
            
        subjects = {"BIO": "Biology", "CHEM": "Chemistry", "PHYS": "Physics", "MATH": "Mathematics"}
        grid = []
        for code, name in subjects.items():
            label = f"📂 {name}"
            # Navigate to Unit List for specific subject and grade
            actions[label] = f"NAV|SCR_LOCK_UNIT_LIST|{code}:{view_grade}"
            
            if len(grid) == 0 or len(grid[-1]) == 2:
                grid.append([label])
            else:
                grid[-1].append(label)
                
        layout = [tab_row] + grid + [["🔙 Back to Lock Registry"]]
        actions["🔙 Back to Lock Registry"] = "NAV|SCR_ADMIN_LOCKS|ROOT"
        
    # SCR_LOCK_UNIT_LIST (Unit Toggles)
    if screen_id == "SCR_LOCK_UNIT_LIST":
        param = extra_vars.get("param")
        if param and ":" in param:
            code, grade_raw = param.split(":")
            grade_num = grade_raw.replace("Grade ", "")
            grade_full = f"Grade {grade_num}"
            
            subj_map = {"BIO": "Biology", "CHEM": "Chemistry", "PHYS": "Physics", "MATH": "Mathematics"}
            subject_name = subj_map.get(code, code)
            
            extra_vars["subject_name"] = subject_name
            extra_vars["grade"] = grade_num
            extra_vars["view_grade"] = grade_num
            
            units = QuestionEngine.list_units(subject_name, grade_full)
            if units:
                from database.models import SystemLock
                db = SessionLocal()
                
                unit_ids = []
                for i, u_title in enumerate(units):
                    u_num = str(i + 1)
                    if "Unit " in u_title:
                        try:
                            t_part = u_title.split(":")[0]
                            u_num = t_part.replace("Unit", "").strip()
                        except: pass
                    
                    unit_id = f"{code}_G{grade_num}_U{u_num}"
                    unit_ids.append((u_title, unit_id))

                targets = [uid for _, uid in unit_ids]
                locks = db.query(SystemLock).filter(
                    SystemLock.lock_type == "UNIT",
                    SystemLock.lock_target.in_(targets)
                ).all()
                lock_map = {l.lock_target: l.is_locked for l in locks}
                db.close()
                
                grid = []
                for u_title, u_id in unit_ids:
                    is_locked = lock_map.get(u_id, False)
                    status = "🔴" if is_locked else "🔵"
                    
                    display_title = u_title
                    if len(display_title) > 20: 
                        display_title = display_title[:18] + ".."
                    
                    label = f"{display_title} {status}"
                    actions[label] = f"ACT|LOCK|TOGGLE_UNIT|{u_id}"
                    grid.append([label])
                
                layout = grid + [["🔙 Back to Units"]]
                actions["🔙 Back to Units"] = f"NAV|SCR_LOCK_UNITS|{grade_num}"

    # --- Translation Pass (Applied to final layout) ---
    header_key = f"{screen_id.replace('SCR_', '')}_HEADER"
    if lang in TRANSLATIONS and header_key in TRANSLATIONS[lang]:
        screen["header_text"] = TRANSLATIONS[lang][header_key]
    
    label_map = {
        "🚀 Start Practice": "START_PRACTICE",
        "⚙️ Set Up Profile": "SETUP_PROFILE",
        "🎓 Start Practice Hub": "START_PRACTICE_HUB",
        "🔍 Advanced Practice": "ADVANCED_PRACTICE",
        "💬 Practice with Friends": "PRACTICE_WITH_FRIENDS",
        "📊 My Progress": "PROGRESS",
        "🏆 Leaderboard": "LEADERBOARD",
        "⚡ Random Quiz": "RANDOM_QUIZ",
        "⚙️ Settings": "SETTINGS_HUB",
        "❓ Help": "HELP_HUB",
        "🛠️ Admin Dashboard": "ADMIN_HUB",
        "🧬 Biology": "BIO", "🧪 Chemistry": "CHEM", "⚛️ Physics": "PHYS", "📐 Mathematics": "MATH",
        "📊 Academic Progress": "ACADEMIC_PROGRESS", "🏠 Home": "HOME_LABEL",
        "🔙 Back to Subjects": "BACK_LABEL", "🔙 Back to Settings": "BACK_LABEL",
        "🔙 Back to Grades": "BACK_LABEL", "🔙 Back to Units": "BACK_LABEL",
        "🔙 Back to Arena": "BACK_LABEL", "🔙 Back to Admin": "BACK_LABEL",
        "🌐 Language: {current_lang}": "LANG_LABEL",
        "🔔 Notifications: {notif_status}": "NOTIF_LABEL",
        "🧹 Reset Progress": "RESET_LABEL", "👤 Profile": "PROFILE_LABEL",
        "⚡ Review All Units": "REVIEW_ALL",
        "📂 Unit Study Guides": "DOWNLOAD_PDFS",
        "📂 PDFs & Files": "DOWNLOAD_PDFS", # Fallback
        "⚡ Review: Part 1": "REVIEW_P1",
        "⚡ Review: Part 2": "REVIEW_P2",
        "⚡ Review: Part 3": "REVIEW_P3",
        "🔄 Restart Unit": "RESTART_UNIT",
        "➡️ Next Unit": "NEXT_UNIT",
        "🔄 Restart Part": "RESTART_PART",
        "➡️ Next Part": "NEXT_PART"
    }
    
    # Add dynamic labels to map to ensure they translate
    for row in layout:
        for lbl in row:
            if "Retry Mistakes" in lbl: label_map[lbl] = "REVIEW_MISTAKES"
            if "Try Skipped" in lbl: label_map[lbl] = "REVIEW_SKIPPED"

    translated_layout = []
    for row in layout:
        new_row = []
        for lbl in row:
            t_key = label_map.get(lbl)
            if lang in TRANSLATIONS and t_key and t_key in TRANSLATIONS[lang]:
                t_lbl_template = TRANSLATIONS[lang][t_key]
                # If it's a dynamic label with count
                if "{count}" in t_lbl_template:
                    count = extra_vars.get("mistake_count" if "MISTAKES" in t_key else "skipped_count", 0)
                    t_lbl = t_lbl_template.replace("{count}", str(count))
                else:
                    t_lbl = t_lbl_template
                
                if lbl in actions:
                    actions[t_lbl] = actions[lbl] 
                new_row.append(t_lbl)
            else:
                new_row.append(lbl)
        translated_layout.append(new_row)
    layout = translated_layout

    # Build and Render
    progress_recs = get_all_user_progress(user_obj.id)
    text = replace_variables(screen.get("header_text", ""), user_id, telegram_id, extra_vars, user_obj=user_obj, progress_records=progress_recs)
    # Filter layout for Admin buttons
    from config import ADMIN_IDS
    filtered_layout = []
    for row in layout: # Use the translated and dynamically generated layout
        filtered_row = []
        for btn_label in row:
            if "Admin" in btn_label and telegram_id not in ADMIN_IDS:
                continue
            filtered_row.append(btn_label)
        if filtered_row:
            filtered_layout.append(filtered_row)
    
    keyboard = build_keyboard(filtered_layout, actions, user_id, telegram_id, extra_vars, user_obj=user_obj, progress_records=progress_recs) # Use filtered_layout and existing actions
    
    try:
        return bot.edit_message_text(chat_id=telegram_id, message_id=message_id, text=text, reply_markup=keyboard, parse_mode="Markdown") if message_id else bot.send_message(chat_id=telegram_id, text=text, reply_markup=keyboard, parse_mode="Markdown")
    except Exception as e:
        if "Message is not modified" in str(e): return None
        print(f"[RENDER] Markdown failed, falling back to plain text: {e}")
        try:
            return bot.edit_message_text(chat_id=telegram_id, message_id=message_id, text=text, reply_markup=keyboard) if message_id else bot.send_message(chat_id=telegram_id, text=text, reply_markup=keyboard)
        except:
            return bot.send_message(chat_id=telegram_id, text=text, reply_markup=keyboard)
