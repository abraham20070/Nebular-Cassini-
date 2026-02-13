"""
Callback router - routes ACTION|SCREEN|PARAM callbacks to appropriate handlers
"""
import sys
import os
import json
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import datetime, timedelta
import datetime as dt_lib
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from handlers.quiz_handler import handle_answer_selection, next_question, start_quiz_session, skip_question, start_next_batch, replay_batch, start_next_part, start_review_session, start_smart_review, start_random_quiz
from handlers.navigation import navigate_to, go_back, go_home
from handlers.screen_renderer import render_screen
import handlers.game_handler as gh
from database.crud import (
    get_or_create_user, get_or_create_session, update_session_state, 
    flag_question, add_to_review_queue, get_challenge, SessionLocal
)
from database.models import User as UserModel, Progress as ProgressModel, FlaggedQuestion, Session as SessionModel, ReviewQueue, Challenge, SystemLock
from utils.question_engine import QuestionEngine
from utils.pdf_generator import generate_unit_pdf, generate_all_units_pdf
from utils.lock_manager import is_content_locked
from sqlalchemy import func
import traceback

def escape_md(val):
    if not val or not isinstance(val, str): return str(val)
    return val.replace("*", "\\*").replace("_", "\\_").replace("`", "\\`")


def parse_callback(callback_data):
    """
    Parse callback data in ACTION|SCREEN|PARAM format.
    
    Returns:
        tuple: (action, screen, param)
    """
    parts = callback_data.split("|")
    action = parts[0] if len(parts) > 0 else None
    screen = parts[1] if len(parts) > 1 else None
    # Support multi-part parameters by joining all trailing parts
    param = "|".join(parts[2:]) if len(parts) > 2 else None
    
    return action, screen, param


def route_callback(bot, update):
    """
    Route a callback query to the appropriate handler.
    """
    query = update.callback_query
    callback_data = query.data
    telegram_id = query.from_user.id
    username = query.from_user.username
    full_name = query.from_user.full_name or query.from_user.first_name
    
    # Ensure user exists in database
    user = get_or_create_user(telegram_id, username, full_name)
    
    # Parse callback
    action, screen, param = parse_callback(callback_data)
    print(f"DEBUG: Callback received from {user.full_name} ({telegram_id}): {action}|{screen}|{param}")
    
    # --- LOCK ENFORCEMENT ---
    try:
        locked, reason = is_content_locked(telegram_id, action, screen, param)
        if locked:
            query.answer(reason, show_alert=True)
            return
        elif reason and "Admin Bypass" in reason:
             # Admin is bypassing a lock - show toast but proceed
             try:
                 query.answer(reason, show_alert=False)
             except: pass 
    except Exception as e:
        print(f"[LOCK CHECK FAIL] {e}")
        # Fail open if check fails to prevent system lockout due to bug
    
    # Route based on action
    try:
        if action == "NAV":
            handle_navigation(bot, query, screen, param)
        elif action == "ACT":
            handle_action(bot, query, screen, param)
        elif action == "ANS":
            handle_answer(bot, query, screen, param)
        else:
            query.answer("Unknown action")
    except Exception as e:
        print(f"[CALLBACK ERROR] {traceback.format_exc()}")
        try:
            # Polite user-facing error
            query.answer("⚠️ The bot is currently under repair. Please try again in a moment.", show_alert=True)
        except: pass

    # Answer the callback query (removes loading indicator)
    query.answer()


def handle_navigation(bot, query, screen, param):
    """Handle NAV actions (screen navigation)"""
    telegram_id = query.from_user.id
    
    # Pattern: NAV|SCR_PDF_VAULT|CURR_CONTEXT
    if (screen == "SCR_PDF_VAULT" or screen == "SCR_REVIEW_HUB") and param == "CURR_CONTEXT":
        user = get_or_create_user(telegram_id, None, "User")
        session = get_or_create_session(user.id)
        # Use the param from SCR_UNITS which is "BIO:Grade 12"
        return navigate_to(bot, telegram_id, screen, param=session.current_param, add_to_stack=True)

    # Pattern: NAV|SCR_UNITS|BIO:Grade 12 - Sync User Grade
    if screen == "SCR_UNITS" and param and ":" in param:
        parts = param.split(":")
        if len(parts) == 2:
            try:
                # Extract grade number
                g_str = parts[1]
                g_num = int(g_str.split(" ")[1]) if " " in g_str else int(g_str) if g_str.isdigit() else 0
                if g_num > 0:
                    user = get_or_create_user(telegram_id, None, "User")
                    # Update grade directly
                    db = SessionLocal()
                    u = db.query(UserModel).filter(UserModel.id == user.id).first()
                    u.current_grade = g_num
                    db.commit()
                    db.close()
            except: pass

    # Debug log for navigation
    print(f"NAV: Screen={screen}, Param={param}")

    if screen == "SCR_HUB" or screen == "HOME" or (screen == "SCR_HUB" and param == "ROOT"):
        return go_home(bot, telegram_id)
        
    if screen == "BACK" or param == "BACK":
        return go_back(bot, telegram_id)

    # Pattern: NAV|SCR_QUIZ_PRES|BIO:G10:U1
    if screen == "SCR_QUIZ_PRES" and param and ":" in param:
        parts = param.split(":")
        if len(parts) == 3:
            subject, grade, unit = parts
            start_quiz_session(bot, telegram_id, subject, grade, unit)
            return

    # Screen setup for persistent stack
    game_screens = ["SCR_SPEEDRUN_SETUP", "SCR_SURVIVAL_SETUP", "SCR_MULTIPLAYER_HUB", 
                   "SCR_MP_SUBJ_SELECT", "SCR_PROFILE_SETTINGS", "SCR_GRADE_SELECT"]
    
    is_root = param == "ROOT"
    add_to_stack = not is_root or (screen in game_screens)
    
    msg = navigate_to(bot, telegram_id, screen, param=param if not is_root else None, add_to_stack=add_to_stack)
    if msg is None:
        # Provide a subtle toast if content didn't change (e.g. clicking same grade)
        if screen == "SCR_STATS" and param and param.isdigit():
             query.answer(f"📍 Already viewing Grade {param}")
        elif screen == "SCR_GAMEMODE" and param and param.isdigit():
             query.answer(f"📍 Already viewing Grade {param}")
        else:
             query.answer() # Still need to answer the callback to stop the loading spinner
    else:
        query.answer()


def handle_action(bot, query, screen, param):
    """Handle ACT actions (button actions)"""
    telegram_id = query.from_user.id
    user = get_or_create_user(telegram_id, None, "User")
    
    if screen == "QUIZ":
        if param == "LOAD_NEXT":
            next_question(bot, telegram_id)
        elif param == "SKIP":
            skip_question(bot, telegram_id)
        elif param == "PIN":
             # Save to ReviewQueue with PINNED status
             session = get_or_create_session(user.id)
             if session.quiz_state:
                 qs = json.loads(session.quiz_state)
                 idx = qs.get("current_index", 0)
                 q_list = qs.get("questions", [])
                 if idx < len(q_list):
                     q = q_list[idx]
                     add_to_review_queue(
                         user_id=user.id,
                         question_id=q["question_id"],
                         status="PINNED",
                         subject=qs["subject"],
                         grade=int(qs["grade"].split(" ")[1]) if " " in str(qs["grade"]) else int(qs["grade"]) if str(qs["grade"]).isdigit() else 9,
                         unit=q.get("source_unit", qs["unit"])
                     )
                     query.answer("📌 Question pinned for later review!", show_alert=True)
                 else:
                     query.answer("Error: No active question.", show_alert=True)
             else:
                 query.answer("Error: Quiz state lost.", show_alert=True)

        elif param == "FLAG":
             # Redirect to reporting options to pick a reason
             navigate_to(bot, telegram_id, "SCR_REPORT_OPTIONS", add_to_stack=False)
             
        elif param == "ADD_NOTE":
             query.answer("📝 Record a personal note.", show_alert=True)
        elif param == "LOAD_NEW_BATCH":
             start_next_batch(bot, telegram_id)
        elif param == "REPLAY":
             replay_batch(bot, telegram_id)
        elif param == "LOAD_NEXT_PART":
             start_next_part(bot, telegram_id)
        elif param in ["REVIEW_1", "REVIEW_2", "REVIEW_3"]:
             session = get_or_create_session(user.id)
             section_num = int(param.split("_")[1])
             
             # Robust context extraction
             context_str = str(session.current_param)
             if ":" in context_str:
                 parts = context_str.split(":")
                 sub, grade = parts[0], parts[1]
                 # Ensure grade is formatted correctly (e.g. "Grade 12")
                 grade_clean = grade.replace("Grade", "").strip()
                 start_review_session(bot, telegram_id, sub, f"Grade {grade_clean}", section_num=section_num)
             else:
                 query.answer("Error: Subject/Grade context not found in session.")
        elif param == "SHOW_FORMULA":
             query.answer("🧮 Showing unit formulas.", show_alert=True)
        elif param == "REVIEW_MISTAKES" or param == "REVIEW_SKIPPED" or param == "REVIEW_PINNED":
             session = get_or_create_session(user.id)
             if param == "REVIEW_MISTAKES": review_type = "MISTAKE"
             elif param == "REVIEW_SKIPPED": review_type = "SKIPPED"
             else: review_type = "PINNED"
             
             context_str = str(session.current_param)
             if ":" in context_str:
                 parts = context_str.split(":")
                 sub, grade = parts[0], parts[1]
                 grade_clean = grade.replace("Grade", "").strip()
                 start_smart_review(bot, telegram_id, sub, f"Grade {grade_clean}", review_type=review_type)
             else:
                query.answer("Error: Subject/Grade context not found.")
        elif param == "UNIT_LOCKED":
             query.answer("🔒 This unit is locked! Complete the previous unit with 80%+ accuracy to unlock.", show_alert=True)
        elif param.startswith("START_RANDOM_QUIZ") or param.startswith("RANDOM"):
             # Check for specific grade in param like START_RANDOM_QUIZ|12
             target_grade = None
             if "|" in param:
                 try:
                     target_grade = int(param.split("|")[1])
                     print(f"[ROUTER] Parsed Grade for Random Quiz: {target_grade}")
                 except: 
                     print(f"[ROUTER] Failed to parse grade from {param}")
                 
             start_random_quiz(bot, telegram_id, grade=target_grade)
        else:
             query.answer(f"Quiz Action: {param}")


    elif screen == "SET":
        db = SessionLocal()
        user_db = db.query(UserModel).filter(UserModel.id == user.id).first()
        
        if param.startswith("LANG|"):
            lang_code = param.split("|")[1]
            lang_name = "English" if lang_code == "EN" else "Amharic"
            user_db.language = lang_code
            db.commit()
            query.answer(f"🌐 Language set to {lang_name}", show_alert=True)
            navigate_to(bot, telegram_id, "SCR_SETTINGS", add_to_stack=False)
            db.close()
            
        elif param == "TOGGLE_LANG":
            new_lang = "AM" if user_db.language == "EN" else "EN"
            user_db.language = new_lang
            db.commit()
            lang_name = "Amharic" if new_lang == "AM" else "English"
            query.answer(f"🌐 Language set to {lang_name}", show_alert=True)
            navigate_to(bot, telegram_id, "SCR_SETTINGS", add_to_stack=False)
            db.close()
            
        elif param == "TOGGLE_NOTIF":
            new_status = not user_db.notifications_enabled
            user_db.notifications_enabled = new_status
            db.commit()
            status_text = "ON" if new_status else "OFF"
            query.answer(f"🔔 Notifications turned {status_text}", show_alert=True)
            navigate_to(bot, telegram_id, "SCR_SETTINGS", add_to_stack=False)
            db.close()
            
        elif param.startswith("UPDATE_GRADE|") or param.startswith("ONBOARD_GRADE|"):
            try:
                g_num = int(param.split("|")[1])
                user_db.current_grade = g_num
                db.commit()
                query.answer(f"✅ Grade set to {g_num}", show_alert=True)
                
                # Navigate: Profile settings for updates, Home for onboarding
                target = "SCR_HUB" if "ONBOARD" in param else "SCR_PROFILE_SETTINGS"
                navigate_to(bot, telegram_id, target, add_to_stack=False)
            except:
                query.answer("❌ Error updating grade.")
            finally:
                db.close()
                
        elif param == "RESET_CONFIRM":
            try:
                db.query(ProgressModel).filter(ProgressModel.user_id == user.id).delete()
                db.query(SessionModel).filter(SessionModel.user_id == user.id).delete()
                if user_db:
                    user_db.total_xp = 0
                    user_db.level = 1
                    user_db.streak_count = 0
                    user_db.current_grade = 9
                db.commit()
                query.answer("✅ All progress has been reset.", show_alert=True)
                navigate_to(bot, telegram_id, "SCR_HUB", add_to_stack=False)
            except Exception as e:
                db.rollback()
                query.answer(f"❌ Error resetting progress: {str(e)}", show_alert=True)
            finally:
                db.close()
        elif param == "SHARE_BOT":
            bot_username = bot.get_me().username
            share_url = f"https://t.me/share/url?url=https://t.me/{bot_username}&text=Check%20out%20this%20amazing%20Scholar%20System%20bot%20for%20G9-12%20students!"
            
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("📤 Share Now", url=share_url)]])
            
            share_msg = f"🌟 *Help your friends succeed!*\n\nYour personal link: `https://t.me/{bot_username}`\n\nTap the button below to share the bot with your friends or study groups! 🚀"
            bot.send_message(chat_id=telegram_id, text=share_msg, reply_markup=kb, parse_mode="Markdown")
            query.answer("Check your messages!")
        else:
            db.close()
            query.answer(f"Action: {screen}|{param}")

    elif screen == "SPEEDRUN":
        try:
            session = get_or_create_session(user.id)
            # Use quiz_state as a temporary setup container before launch
            setup = {}
            if session.quiz_state:
                try: 
                    setup = json.loads(session.quiz_state)
                    # If it's a real game state, don't use it for setup
                    if "mode" in setup and setup["mode"] == "SPEEDRUN": setup = {}
                except: setup = {}
            
            # Default values
            if not setup:
                setup = {"dur": 30, "cnt": 30, "subj": "MIXED"}
            
            if param.startswith("DUR|"):
                setup["dur"] = int(param.split("|")[1])
                update_session_state(user.id, quiz_state=setup)
                render_screen(bot, user.id, telegram_id, "SCR_SPEEDRUN_HUB", query.message.message_id, setup)
                query.answer(f"⏱️ Duration set to {setup['dur']} min")
                
            elif param.startswith("CNT|"):
                setup["cnt"] = int(param.split("|")[1])
                update_session_state(user.id, quiz_state=setup)
                render_screen(bot, user.id, telegram_id, "SCR_SPEEDRUN_HUB", query.message.message_id, setup)
                query.answer(f"🔢 Count set to {setup['cnt']} Qs")
                
            elif param.startswith("SUBJ|"):
                setup["subj"] = param.split("|")[1]
                update_session_state(user.id, quiz_state=setup)
                
                # LAUNCH IMMEDIATELY as requested
                dur_secs = setup["dur"] * 60
                gh.start_speedrun(bot, telegram_id, dur_secs, subject_code=setup["subj"], count=setup["cnt"])
                query.answer("🚀 Launching MCQ Practice!")
            
            elif param == "LAUNCH":
                # Fallback for old buttons if any
                dur_secs = setup["dur"] * 60
                gh.start_speedrun(bot, telegram_id, dur_secs, subject_code=setup["subj"], count=setup["cnt"])
                query.answer("🚀 Launching Timed Practice!")
            
            # Legacy fallback
            elif param.startswith("START_"):
                raw_param = param.replace("START_", "")
                parts = raw_param.split(":")
                gh.start_speedrun(bot, telegram_id, int(parts[0]), subject_code=parts[1], count=int(parts[2]))

        except Exception as e:
            print(f"ERROR in SPEEDRUN Router: {e}")
            query.answer("❌ Error setting up session.")

    elif screen == "SURVIVAL":
        try:
            # START_Grade:Subj or START_Subj
            raw_param = param.replace("START_", "")
            parts = raw_param.split(":")
            grade = None
            if parts[0] in ["9", "10", "11", "12"]:
                grade = int(parts[0])
                subj_code = parts[1]
            else:
                subj_code = parts[0]
                
            print(f"DEBUG: Starting SURVIVAL [Grade={grade}, Subj={subj_code}]")
            gh.start_survival(bot, telegram_id, subj_code, grade=grade)
        except Exception as e:
            print(f"ERROR: Failed to start SURVIVAL: {e}")
            query.answer(f"Error starting game: {e}", show_alert=True)

    elif screen == "GAME":
        if param == "REPLAY":
             session = get_or_create_session(user.id)
             state = json.loads(session.quiz_state) if session.quiz_state else {}
             if state.get("mode") == "SPEEDRUN":
                 gh.start_speedrun(bot, telegram_id, state.get("duration", 60), 
                                   subject_code=state.get("subject_code"), 
                                   count=state.get("count", 20))
             elif state.get("mode") == "SURVIVAL":
                 subj_map_rev = {"Biology": "BIO", "Chemistry": "CHEM", "Physics": "PHYS", "Mathematics": "MATH"}
                 gh.start_survival(bot, telegram_id, subj_map_rev.get(state.get("subject"), "BIO"))
             elif state.get("mode") == "CHALLENGE":
                 challenge = get_challenge(state.get("unit_id"))
                 if challenge:
                     gh.start_challenge_session(bot, telegram_id, challenge)
                 else:
                     query.answer("Challenge expired.")
             else:
                 query.answer("No active game to replay.")
        else:
             query.answer(f"Game Action: {param}")

    elif screen == "MP":
        if param.startswith("GENERATE|"):
            subj = param.split("|")[1]
            gh.start_multiplayer_generation(bot, telegram_id, subj)
        elif param == "SHARE_TRIGGER":
            gh.handle_mp_share(bot, telegram_id)
        else:
            query.answer(f"❓ Unknown multiplayer action: {param}", show_alert=True)

    elif screen == "RANK":
        # Handle leaderboard scope switching
        if param == "SWITCH_GLOBAL":
            # Render leaderboard with Global scope
            session = get_or_create_session(user.id)
            navigate_to(bot, telegram_id, "SCR_RANKING", param=None, add_to_stack=False, extra_vars={"leaderboard_scope": "Global"})
            query.answer("🌍 Switched to Global Leaderboard")
        elif param == "SWITCH_WEEKLY":
            # Render leaderboard with Weekly scope
            session = get_or_create_session(user.id)
            navigate_to(bot, telegram_id, "SCR_RANKING", param=None, add_to_stack=False, extra_vars={"leaderboard_scope": "Weekly"})
            query.answer("📅 Switched to Weekly Leaderboard")
        else:
            query.answer(f"Leaderboard action: {param}")


    elif screen == "HELP":
        help_topics = {
            "HOW_TO_PLAY": """📖 *Usage Guide - Complete Walkthrough*

━━━━━━━━━━━━━━
📚 *Practice System*
━━━━━━━━━━━━━━

*1️⃣ Choose Your Subject*
• Select from Biology, Chemistry, Physics, or Mathematics
• Pick your current grade level (9-12)
• Choose a unit to begin your practice

*2️⃣ Answer Questions*
• Each unit contains curated MCQ batches
• Select A, B, C, or D for each question
• Receive instant feedback and textbook explanations

*3️⃣ Track Your Mastery*
• ✅ Correct answers earn you Mastery Points
• 📊 Complete units to improve your curriculum coverage
• 🎯 Achieve 80%+ accuracy to master a specific topic

━━━━━━━━━━━━━━
⭐ *Point & Leveling System*
━━━━━━━━━━━━━━

• Earn points for every correct practice attempt
• Level up as you master more content
• Higher levels reflect your dedication and progress
• Milestone Formula: Level² × 100 points needed

━━━━━━━━━━━━━━
🎯 *Learning Streak*
━━━━━━━━━━━━━━

• Practice daily to build your learning streak
• Consistency is the most effective way to prepare for exams
• 7-day streaks earn you the 🔥 dedication badge
• Streaks reflect your daily commitment to study

━━━━━━━━━━━━━━
📈 *Progress Indicators*
━━━━━━━━━━━━━━

• Unit Progress Bar: Shows your session completion
• Mastery Percentage: Your overall score for the grade
• Phase System: Baseline → Balanced → Exam Focused
• Completion badges mark your academic milestones

━━━━━━━━━━━━━━
⚡ *Practice Modes*
━━━━━━━━━━━━━━

*⏱️ Timed Practice*
• Test your performance under time constraints
• Choose durations from 2 to 60 minutes
• Aligned with the pace of national entrance exams
• Perfect for building exam-day endurance

*🎯 Precision Test*
• Focus on perfect accuracy
• See how many you can get right without any errors
• Encourages careful reading and deep understanding

*💬 Practice with Friends*
• Join a shared practice session with 10 questions
• Compare results on the same set of questions
• Engage in healthy academic competition!

Ready to begin your practice? 🚀""",

            "FEATURES": """🛠️ *Practice Features - Everything You Need*

━━━━━━━━━━━━━━
📚 *Academic Tools*
━━━━━━━━━━━━━━

*📖 Unit-Based Practice*
• Structured exactly like your Ethiopian textbooks
• Progressive difficulty across three study phases
• Systematic coverage of the entire syllabus
• Feedback icons track your progress per unit

*⚡ Review Hub*
• Review All Units: Practice everything in your grade
• Divided into 3 manageable sections (Part 1, 2, 3)
• Retry Mistakes: Focus on areas where you need more work
• Try Skipped: Return to questions you previously bypassed

*📌 Pin System*
• Pin difficult questions to study them again later
• Build a personal collection of challenging concepts
• View your pinned library anytime from the Practice Hub

━━━━━━━━━━━━━━
📂 *Unit Study Guides*
━━━━━━━━━━━━━━

• Download unit-based question banks as PDF files
• Study offline for your exams anywhere
• Includes correct answers and explanations at the end
• Download individual units or full grade volumes
• High-quality, modern guides for serious preparation

━━━━━━━━━━━━━━
📊 *Progress Tracking*
━━━━━━━━━━━━━━

*Academic Dashboard*
• View your performance across all science subjects
• Monitor your completion and mastery stats
• Identify your strongest and weakest topics
• Track grade-specific progress independently

*Detailed Academic Audit*
• Unit-by-unit performance breakdown
• Specific mastery percentages
• Tracks your current phase in the practice cycle
• See where you stand in subject-wide rankings

━━━━━━━━━━━━━━
🏆 *Leaderboard System*
━━━━━━━━━━━━━━

• Global Rankings: Top performing students nationwide
• Weekly Rankings: Fresh competition every week
• View points and rank based on accurate practice
• Earn medals for consistent top-tier performance 🥇🥈🥉

━━━━━━━━━━━━━━
🚩 *Quality Assurance*
━━━━━━━━━━━━━━

*Report Issues*
• Technical Error: Formatting or display issues
• Incorrect Answer: If you find a wrong answer key
• Scientific Error: Content inaccuracy from textbook
• Typo/Formatting: Minor text or label issues

Your feedback ensures our content remains 100% accurate!

━━━━━━━━━━━━━━
⚙️ *Customization*
━━━━━━━━━━━━━━

• Language: Toggle between English and Amharic
• Notifications: Manage your study reminders
• Grade Selection: Update your current grade level
• Progress Reset: Start fresh for a new semester
• Profile Settings: View your student identification

━━━━━━━━━━━━━━
⚡ *Random Practice*
━━━━━━━━━━━━━━

• Mixed questions from all subjects
• Select any grade level for a quick test
• Comprehensive review of various topics
• 10 questions pulled randomly from the entire pool
• No pressure, just continuous improvement!

All tools are built to help you master your curriculum! 💪""",

            "CURRICULUM": """📐 *Curriculum Information*

━━━━━━━━━━━━━━
📚 *Content Source*
━━━━━━━━━━━━━━

Our questions are carefully extracted from:
• Official Ethiopian Ministry of Education textbooks
• Grades 9-12 Science curriculum
• Biology, Chemistry, Physics, and Mathematics
• Aligned with national exam standards

━━━━━━━━━━━━━━
🎯 *Why This Matters*
━━━━━━━━━━━━━━

✅ *Exam-Relevant Content*
• Questions match actual exam patterns
• Topics aligned with your syllabus
• Practice what you'll actually be tested on

✅ *Comprehensive Coverage*
• Every unit from your textbooks
• All major topics included
• Progressive difficulty levels

✅ *Quality Assurance*
• Verified against official sources
• Regular content updates
• Error reporting system for quality control

━━━━━━━━━━━━━━
📖 *Subject Coverage*
━━━━━━━━━━━━━━

*🧬 Biology*
• Cell Biology & Genetics
• Ecology & Evolution
• Human Anatomy & Physiology
• Plant Biology
• And more...

*🧪 Chemistry*
• Atomic Structure
• Chemical Bonding
• Organic Chemistry
• Stoichiometry
• And more...

*⚛️ Physics*
• Mechanics & Motion
• Electricity & Magnetism
• Waves & Optics
• Thermodynamics
• And more...

*📐 Mathematics*
• Algebra & Functions
• Geometry & Trigonometry
• Calculus & Analysis
• Statistics & Probability
• And more...

━━━━━━━━━━━━━━
🎓 *Grade Levels*
━━━━━━━━━━━━━━

• Grade 9: Foundation concepts
• Grade 10: Intermediate topics
• Grade 11: Advanced preparation
• Grade 12: Exam-focused content

Each grade builds on previous knowledge!

━━━━━━━━━━━━━━
📊 *Learning Phases*
━━━━━━━━━━━━━━

*Phase 1: Baseline*
• Introduction to unit concepts
• Fundamental questions
• Build your foundation

*Phase 2: Balanced*
• Mixed difficulty levels
• Comprehensive topic coverage
• Strengthen understanding

*Phase 3: Exam Biased*
• Exam-style questions
• Higher difficulty
• Final preparation

Progress through phases by mastering content!

━━━━━━━━━━━━━━
🔄 *Content Updates*
━━━━━━━━━━━━━━

• Regular quality checks
• New questions added periodically
• User feedback incorporated
• Curriculum changes reflected

Your success is our priority! 🎯""",

            "SUPPORT": """📞 *Contact Support*

━━━━━━━━━━━━━━
🆘 *Need Help?*
━━━━━━━━━━━━━━

We're here to assist you with any issues or questions!

━━━━━━━━━━━━━━
📧 *Contact Methods*
━━━━━━━━━━━━━━

*Official Support*
• Telegram: @NebularAdmin
• Response time: 24-48 hours
• Available for all users

━━━━━━━━━━━━━━
🚩 *Report Issues*
━━━━━━━━━━━━━━

*In-App Reporting*
Use the 🚩 Flag button during quizzes to report:
• Wrong answers
• Technical errors
• Content mistakes
• Formatting issues

Your reports are reviewed by our team!

━━━━━━━━━━━━━━
❓ *Common Issues*
━━━━━━━━━━━━━━

*Q: Button not working?*
A: Try restarting the bot with /start

*Q: Progress not saving?*
A: Check your internet connection and try again

*Q: Can't unlock next unit?*
A: You need 80%+ accuracy on current unit

*Q: Lost my streak?*
A: Streaks reset if you don't practice daily

*Q: How to get AI Tutor access?*
A: Contact admin for authorization

━━━━━━━━━━━━━━
💡 *Feature Requests*
━━━━━━━━━━━━━━

Have ideas to improve the bot?
• Contact @NebularAdmin
• Describe your suggestion
• We review all feedback!

━━━━━━━━━━━━━━
🐛 *Found a Bug?*
━━━━━━━━━━━━━━

Please report with:
• What you were doing
• What went wrong
• Screenshots if possible
• Your user ID: {telegram_id}

━━━━━━━━━━━━━━
🎓 *Academic Support*
━━━━━━━━━━━━━━

For content-related questions:
• Use the AI Tutor feature (if authorized)
• Review question explanations
• Check the PDF study guides
• Practice with Review Hub

━━━━━━━━━━━━━━
⚡ *Quick Tips*
━━━━━━━━━━━━━━

• Use /start to reset the bot
• Check Settings for customization
• Review your Progress regularly
• Practice daily for best results
• Join the Leaderboard competition!

We're committed to your success! 🌟

*Remember: Your feedback makes us better!*"""
        }
        text = help_topics.get(param, "❓ *Unknown Help Topic*\n\nPlease select a valid topic from the Help menu.")
        
        # Replace variables in help text
        text = text.replace("{telegram_id}", str(telegram_id))
        
        # We don't want to navigate, just edit text and add a BACK button
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Help", callback_data="NAV|SCR_HELP|ROOT")]])
        bot.edit_message_text(chat_id=telegram_id, message_id=query.message.message_id, text=text, reply_markup=kb, parse_mode="Markdown")

    
    elif screen == "PDF":
        # Handle PDF download requests
        if param.startswith("DOWNLOAD_UNIT|"):
            # Format: DOWNLOAD_UNIT|BIO:Grade 12:Unit 1
            details = param.replace("DOWNLOAD_UNIT|", "")
            parts = details.split(":")
            
            if len(parts) == 3:
                query.answer("🚀 Generating your modern Study Guide... Please wait.", show_alert=False)
                
                code, grade, unit = parts
                subj_map = {"BIO": "Biology", "CHEM": "Chemistry", "PHYS": "Physics", "MATH": "Mathematics"}
                subject = subj_map.get(code, code)
                
                # Load questions for this unit
                questions, _, unit_title = QuestionEngine.load_unit_questions(subject, grade, unit)
                
                if not questions:
                    query.answer("❌ No questions found for this unit.", show_alert=True)
                    return
                
                # Define output path
                pdf_path = f"{code}_{grade.replace(' ', '')}_{unit.replace(' ', '')}.pdf"
                
                # PRE-CHECK CACHE for instant delivery
                from utils.pdf_generator import CACHE_DIR
                cache_key = f"{subject}_{grade}_{unit_title or unit}".replace(" ", "_").replace(":", "_")
                cached_file = os.path.join(CACHE_DIR, f"{cache_key}.pdf")
                
                if os.path.exists(cached_file):
                    # INSTANT SEND
                    query.answer("🚀 Retrieving Study Guide...", show_alert=False)
                    with open(cached_file, "rb") as f:
                        bot.send_document(
                            chat_id=telegram_id, 
                            document=f, 
                            caption=f"📄 *{subject} - {unit}*\n\n✅ Complete MCQ Study Guide\n📝 {len(questions)} Questions\n✔️ Answers & Explanations Included\n\nGenerated by @NebularCassiniBot",
                            parse_mode="Markdown"
                        )
                    query.answer("✅ Study Guide sent successfully!", show_alert=False)
                    return
                
                # If not cached, continue with generation feedback
                try:
                    bot.edit_message_text(chat_id=telegram_id, message_id=query.message.message_id, text=f"⏳ *Generating Study Guide...*\n\n{subject} - {unit}\n\nPlease wait while we prepare your high-quality PDF.", parse_mode="Markdown")
                    
                    generate_unit_pdf(subject, grade, unit_title or unit, questions, pdf_path)
                    
                    # Send PDF to user
                    with open(pdf_path, "rb") as f:
                        bot.send_document(
                            chat_id=telegram_id, 
                            document=f, 
                            caption=f"📄 *{subject} - {unit}*\n\n✅ Complete MCQ Study Guide\n📝 {len(questions)} Questions\n✔️ Answers & Explanations Included\n\nGenerated by @NebularCassiniBot",
                            parse_mode="Markdown"
                        )
                    
                    # Notify and restore UI
                    query.answer("✅ Study Guide sent successfully!", show_alert=False)
                    render_screen(bot, user.id, telegram_id, "SCR_PDF_VAULT", query.message.message_id, {"param": f"{code}:{grade}"})
                except Exception as e:
                    print(f"[PDF ERROR] {e}")
                    query.answer(f"❌ PDF Generation Error: {str(e)}", show_alert=True)
                finally:
                    # Clean up temporary file
                    if os.path.exists(pdf_path):
                        try:
                            os.remove(pdf_path)
                        except:
                            pass
            else:
                query.answer("❌ Invalid PDF request format.", show_alert=True)
        
        elif param.startswith("DOWNLOAD_ALL|"):
            # Format: DOWNLOAD_ALL|BIO:Grade 12
            details = param.replace("DOWNLOAD_ALL|", "")
            parts = details.split(":")
            
            if len(parts) == 2:
                query.answer("📚 Generating Comprehensive Volume... This may take a few moments.", show_alert=False)
                
                code, grade = parts
                subj_map = {"BIO": "Biology", "CHEM": "Chemistry", "PHYS": "Physics", "MATH": "Mathematics"}
                subject = subj_map.get(code, code)
                
                # Load all units for this subject and grade
                units = QuestionEngine.list_units(subject, grade)
                
                if not units:
                    query.answer("❌ No units found for this subject and grade.", show_alert=True)
                    return
                
                # Load questions for all units
                unit_data_list = []
                total_questions = 0
                for u in units:
                    qs, _, ut = QuestionEngine.load_unit_questions(subject, grade, u)
                    if qs:
                        unit_data_list.append((ut or u, qs))
                        total_questions += len(qs)
                
                if not unit_data_list:
                    query.answer("❌ No content found for this grade.", show_alert=True)
                    return
                
                # Define path
                pdf_path = f"{code}_{grade.replace(' ', '')}_FullVolume.pdf"
                
                # PRE-CHECK CACHE for instant delivery
                from utils.pdf_generator import CACHE_DIR
                cache_key = f"COMPREHENSIVE_{subject}_{grade}_{len(unit_data_list)}".replace(" ", "_")
                cached_file = os.path.join(CACHE_DIR, f"{cached_file_name}.pdf" if 'cached_file_name' in locals() else f"{cache_key}.pdf")
                
                if os.path.exists(cached_file):
                    # INSTANT SEND
                    query.answer("📚 Retrieving Comprehensive Volume...", show_alert=False)
                    with open(cached_file, "rb") as f:
                        bot.send_document(
                            chat_id=telegram_id, 
                            document=f, 
                            caption=f"📚 *{subject} {grade} - Complete Volume*\n\n✅ All Units Included\n📝 {total_questions} Questions\n✔️ Full Answers & Explanations\n\nGenerated by @NebularCassiniBot",
                            parse_mode="Markdown"
                        )
                    query.answer("✅ Complete volume sent successfully!", show_alert=False)
                    return

                try:
                    # Visual feedback
                    bot.edit_message_text(chat_id=telegram_id, message_id=query.message.message_id, text=f"⏳ *Generating Full Volume...*\n\n{subject} - {grade}\n\nThis may take up to 30 seconds. Please stay on this screen.", parse_mode="Markdown")
                    
                    generate_all_units_pdf(subject, grade, unit_data_list, pdf_path)
                    
                    # Send PDF to user
                    with open(pdf_path, "rb") as f:
                        bot.send_document(
                            chat_id=telegram_id, 
                            document=f, 
                            caption=f"📚 *{subject} {grade} - Complete Volume*\n\n✅ All Units Included\n📝 {total_questions} Questions\n✔️ Full Answers & Explanations\n\nGenerated by @NebularCassiniBot",
                            parse_mode="Markdown"
                        )
                    query.answer("✅ Complete volume sent successfully!", show_alert=False)
                    render_screen(bot, user.id, telegram_id, "SCR_PDF_VAULT", query.message.message_id, {"param": f"{code}:{grade}"})
                except Exception as e:
                    print(f"[PDF ERROR] {e}")
                    query.answer(f"❌ PDF Generation Error: {str(e)}", show_alert=True)
                finally:
                    # Clean up temporary file
                    if os.path.exists(pdf_path):
                        try:
                            os.remove(pdf_path)
                        except:
                            pass
            else:
                query.answer("❌ Invalid PDF request format.", show_alert=True)
        else:
            query.answer("❌ Unknown PDF action.", show_alert=True)
    
    elif screen == "ADMIN":
        if param == "GLOBAL_WIPE":
            db = SessionLocal()
            try:
                db.query(Challenge).delete()
                db.query(ReviewQueue).delete()
                db.query(FlaggedQuestion).delete()
                db.query(ProgressModel).delete()
                db.query(SessionModel).delete()
                db.query(UserModel).delete()
                db.commit()
                query.answer("💥 GLOBAL WIPE COMPLETE. System is now empty.", show_alert=True)
                navigate_to(bot, telegram_id, "SCR_WELCOME", add_to_stack=False)
            except Exception as e:
                db.rollback()
                query.answer(f"❌ Wipe Failed: {str(e)}", show_alert=True)
            finally:
                db.close()
                
        elif param == "VIEW_ACTIVE_USERS":
            db = SessionLocal()
            users = db.query(UserModel).order_by(UserModel.last_activity.desc()).limit(20).all()
            total_count = db.query(UserModel).count()
            db.close()
            
            if not users:
                bot.send_message(chat_id=telegram_id, text="📭 *No Active Users*\n\nThe system has no registered users yet.", parse_mode="Markdown")
                query.answer()
                return
            
            lines = ["👥 *Recent Active Users (Top 20)*\n━━━━━━━━━━━━━━"]
            for i, u in enumerate(users, 1):
                last_act = u.last_activity.strftime("%Y-%m-%d %H:%M") if u.last_activity else "Never"
                username_str = f"@{escape_md(u.username)}" if u.username else "No Username"
                lines.append(f"{i}. *{escape_md(u.full_name)}*\n   {username_str} | ID: `{u.telegram_id}`\n   Level {u.level} | {u.total_xp} XP | Grade {u.current_grade}\n   Last Active: {last_act}\n")
            
            lines.append(f"\n━━━━━━━━━━━━━━\n📊 Total Users in System: {total_count}")
            lines.append(f"Last Updated: {datetime.utcnow().strftime('%H:%M:%S')} UTC")
            
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Refresh", callback_data="ACT|ADMIN|VIEW_ACTIVE_USERS")],
                [InlineKeyboardButton("🔙 Back to Admin", callback_data="NAV|SCR_ADMIN|ROOT")]
            ])
            
            try:
                bot.edit_message_text(chat_id=telegram_id, message_id=query.message.message_id, text="\n".join(lines), reply_markup=kb, parse_mode="Markdown")
            except Exception as e:
                if "Message is not modified" in str(e):
                    query.answer("✅ Already up to date")
                else:
                    bot.send_message(chat_id=telegram_id, text="\n".join(lines), reply_markup=kb, parse_mode="Markdown")
            
        elif param.startswith("RESOLVE_FLAG|"):
            q_id = param.split("|")[1]
            db = SessionLocal()
            deleted_count = db.query(FlaggedQuestion).filter(FlaggedQuestion.question_id == q_id).delete()
            db.commit()
            db.close()
            
            if deleted_count > 0:
                query.answer(f"✅ Resolved flags for {q_id}", show_alert=True)
            else:
                query.answer(f"⚠️ No flags found for {q_id}", show_alert=True)
            navigate_to(bot, telegram_id, "SCR_ADMIN_FLAGS", add_to_stack=False)
            
        elif param == "START_ADD_FLOW":
            # Start interactive question addition flow
            query.answer("📝 Question Addition Flow", show_alert=True)
            msg = """📝 *Add New Question - Interactive Mode*

━━━━━━━━━━━━━━
🎯 *Instructions*
━━━━━━━━━━━━━━

This feature allows you to add questions one at a time through an interactive chat flow.

*Steps:*
1️⃣ Send the question stem (text)
2️⃣ Send Option A
3️⃣ Send Option B
4️⃣ Send Option C
5️⃣ Send Option D
6️⃣ Specify correct answer (A/B/C/D)
7️⃣ Send explanation text
8️⃣ Confirm and save

━━━━━━━━━━━━━━
⚠️ *Note*
━━━━━━━━━━━━━━

This feature requires text message handling which is currently in development. For now, please use the JSON/CSV upload options.

━━━━━━━━━━━━━━
💡 *Alternative*
━━━━━━━━━━━━━━

Use "📁 Upload JSON File" or "📄 Upload CSV File" for bulk question import."""
            
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Content Manager", callback_data="NAV|SCR_ADMIN_CONTENT|BACK")]])
            bot.send_message(chat_id=telegram_id, text=msg, reply_markup=kb, parse_mode="Markdown")
            
        elif param == "WAIT_JSON":
            query.answer("📁 JSON Upload Mode", show_alert=True)
            msg = """📁 *Upload JSON Question File*

━━━━━━━━━━━━━━
📋 *Format Required*
━━━━━━━━━━━━━━

Your JSON file should follow this structure:

```json
{
  "subject": "Biology",
  "grade": "Grade 12",
  "unit": "Unit 1",
  "questions": [
    {
      "question_id": "BIO_G12_U1_Q1",
      "question": "What is photosynthesis?",
      "options": {
        "A": "Process of...",
        "B": "Process of...",
        "C": "Process of...",
        "D": "Process of..."
      },
      "correct_answer": "A",
      "explanation": "Photosynthesis is..."
    }
  ]
}
```

━━━━━━━━━━━━━━
📤 *How to Upload*
━━━━━━━━━━━━━━

1. Prepare your JSON file
2. Send it as a document to this chat
3. The bot will validate and import it
4. You'll receive a confirmation

━━━━━━━━━━━━━━
⚠️ *Note*
━━━━━━━━━━━━━━

File upload handling is currently in development. For now, manually add files to the `data/` directory following the existing structure.

*Current Path:*
`e:\\project1\\data\\{subject}\\{grade}\\{unit}.json`"""
            
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Content Manager", callback_data="NAV|SCR_ADMIN_CONTENT|BACK")]])
            bot.send_message(chat_id=telegram_id, text=msg, reply_markup=kb, parse_mode="Markdown")
            
        elif param == "WAIT_CSV":
            query.answer("📄 CSV Upload Mode", show_alert=True)
            msg = """📄 *Upload CSV Question File*

━━━━━━━━━━━━━━
📋 *Format Required*
━━━━━━━━━━━━━━

Your CSV file should have these columns:

```
question_id,subject,grade,unit,question,option_a,option_b,option_c,option_d,correct_answer,explanation
```

*Example Row:*
```
BIO_G12_U1_Q1,Biology,12,1,"What is photosynthesis?","Process A","Process B","Process C","Process D",A,"Photosynthesis is..."
```

━━━━━━━━━━━━━━
📤 *How to Upload*
━━━━━━━━━━━━━━

1. Prepare your CSV file with headers
2. Ensure all fields are properly quoted
3. Send it as a document to this chat
4. The bot will parse and import it
5. You'll receive a validation report

━━━━━━━━━━━━━━
⚠️ *Note*
━━━━━━━━━━━━━━

File upload handling is currently in development. For now, you can:
- Use JSON format (preferred)
- Manually add to data directory
- Contact developer for bulk imports

━━━━━━━━━━━━━━
💡 *Tip*
━━━━━━━━━━━━━━

JSON format is recommended for better structure and easier validation."""
            
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Content Manager", callback_data="NAV|SCR_ADMIN_CONTENT|BACK")]])
            bot.send_message(chat_id=telegram_id, text=msg, reply_markup=kb, parse_mode="Markdown")
            
        elif param == "EXPORT_ALL_DATA":
            # Export all system data
            query.answer("📊 Exporting Data...", show_alert=True)
            db = SessionLocal()
            try:
                users = db.query(UserModel).all()
                progress = db.query(ProgressModel).all()
                flags = db.query(FlaggedQuestion).all()
                
                export_data = {
                    "export_date": datetime.utcnow().isoformat(),
                    "total_users": len(users),
                    "total_progress_records": len(progress),
                    "total_flagged_questions": len(flags),
                    "users": [
                        {
                            "telegram_id": u.telegram_id,
                            "username": u.username,
                            "full_name": u.full_name,
                            "join_date": u.join_date.isoformat() if u.join_date else None,
                            "current_grade": u.current_grade,
                            "level": u.level,
                            "total_xp": u.total_xp,
                            "streak_count": u.streak_count,
                            "language": u.language
                        } for u in users
                    ],
                    "progress": [
                        {
                            "telegram_id": next((u.telegram_id for u in users if u.id == p.user_id), None),
                            "subject": p.subject,
                            "grade": p.grade,
                            "unit_id": p.unit_id,
                            "current_phase": p.current_phase,
                            "completion_percent": p.completion_percent,
                            "questions_attempted": p.questions_attempted,
                            "questions_correct": p.questions_correct
                        } for p in progress
                    ],
                    "flagged_questions": [
                        {
                            "question_id": f.question_id,
                            "flag_count": f.flag_count,
                            "reasons": json.loads(f.reasons) if f.reasons else [],
                            "last_flagged": f.last_flagged.isoformat() if f.last_flagged else None
                        } for f in flags
                    ]
                }
                
                # Save to file
                export_filename = f"nebular_export_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
                export_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), export_filename)
                
                with open(export_path, 'w', encoding='utf-8') as f:
                    json.dump(export_data, f, indent=2, ensure_ascii=False)
                
                # Send file to admin
                with open(export_path, 'rb') as f:
                    bot.send_document(
                        chat_id=telegram_id,
                        document=f,
                        caption=f"📊 *System Data Export*\n\n✅ Users: {len(users)}\n✅ Progress Records: {len(progress)}\n✅ Flagged Questions: {len(flags)}\n\n🕐 Exported: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC",
                        parse_mode="Markdown"
                    )
                
                # Clean up
                if os.path.exists(export_path):
                    os.remove(export_path)
                    
                query.answer("✅ Export Complete!", show_alert=True)
                
            except Exception as e:
                query.answer(f"❌ Export Failed: {str(e)}", show_alert=True)
                print(f"[ADMIN] Export Error: {e}")
            finally:
                db.close()
                
        elif param == "CLEAR_ALL_FLAGS":
            # Clear all flagged questions
            db = SessionLocal()
            try:
                count = db.query(FlaggedQuestion).count()
                db.query(FlaggedQuestion).delete()
                db.commit()
                query.answer(f"✅ Cleared {count} flagged questions", show_alert=True)
                navigate_to(bot, telegram_id, "SCR_ADMIN_FLAGS", add_to_stack=False)
            except Exception as e:
                db.rollback()
                query.answer(f"❌ Failed: {str(e)}", show_alert=True)
            finally:
                db.close()
                
        elif param == "VIEW_SYSTEM_HEALTH":
            # Show detailed system health metrics
            db = SessionLocal()
            try:
                # Gather metrics
                total_users = db.query(UserModel).count()
                active_users_24h = db.query(UserModel).filter(
                    UserModel.last_activity >= datetime.utcnow() - timedelta(days=1)
                ).count()
                active_users_7d = db.query(UserModel).filter(
                    UserModel.last_activity >= datetime.utcnow() - timedelta(days=7)
                ).count()
                
                total_progress = db.query(ProgressModel).count()
                total_questions_attempted = db.query(func.sum(ProgressModel.questions_attempted)).scalar() or 0
                total_questions_correct = db.query(func.sum(ProgressModel.questions_correct)).scalar() or 0
                
                total_xp = db.query(func.sum(UserModel.total_xp)).scalar() or 0
                avg_level = db.query(func.avg(UserModel.level)).scalar() or 0
                max_level = db.query(func.max(UserModel.level)).scalar() or 0
                
                total_sessions = db.query(SessionModel).count()
                active_sessions = db.query(SessionModel).filter(SessionModel.session_active == True).count()
                
                total_review_queue = db.query(ReviewQueue).count()
                total_flags = db.query(FlaggedQuestion).count()
                total_challenges = db.query(Challenge).count()
                
                accuracy = (total_questions_correct / total_questions_attempted * 100) if total_questions_attempted > 0 else 0
                
                msg = f"""🏥 *System Health Report*

━━━━━━━━━━━━━━
👥 *User Metrics*
━━━━━━━━━━━━━━

• Total Users: {total_users}
• Active (24h): {active_users_24h}
• Active (7d): {active_users_7d}
• Retention Rate: {(active_users_7d/total_users*100) if total_users > 0 else 0:.1f}%

━━━━━━━━━━━━━━
📊 *Learning Metrics*
━━━━━━━━━━━━━━

• Progress Records: {total_progress}
• Questions Attempted: {total_questions_attempted:,}
• Questions Correct: {total_questions_correct:,}
• Global Accuracy: {accuracy:.1f}%

━━━━━━━━━━━━━━
⭐ *Gamification*
━━━━━━━━━━━━━━

• Total XP Earned: {total_xp:,}
• Average Level: {avg_level:.1f}
• Highest Level: {max_level}
• XP per User: {(total_xp/total_users) if total_users > 0 else 0:.0f}

━━━━━━━━━━━━━━
🔧 *System Status*
━━━━━━━━━━━━━━

• Total Sessions: {total_sessions}
• Active Sessions: {active_sessions}
• Review Queue: {total_review_queue} items
• Flagged Questions: {total_flags}
• Active Challenges: {total_challenges}

━━━━━━━━━━━━━━
✅ *Health Status*
━━━━━━━━━━━━━━

{"🟢 System Healthy" if total_users > 0 and accuracy > 50 else "🟡 System Operational" if total_users > 0 else "🔴 No Users Yet"}

Last Updated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC"""
                
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Refresh", callback_data="ACT|ADMIN|VIEW_SYSTEM_HEALTH")],
                    [InlineKeyboardButton("🔙 Back to Admin", callback_data="NAV|SCR_ADMIN|ROOT")]
                ])
                
                try:
                    bot.edit_message_text(chat_id=telegram_id, message_id=query.message.message_id, text=msg, reply_markup=kb, parse_mode="Markdown")
                except Exception as e:
                    if "Message is not modified" in str(e):
                        query.answer("✅ Already up to date")
                    else:
                        bot.send_message(chat_id=telegram_id, text=msg, reply_markup=kb, parse_mode="Markdown")
                
            except Exception as e:
                try:
                    query.answer(f"❌ Error: {str(e)}", show_alert=True)
                except: pass
            finally:
                db.close()
                
        elif param == "VIEW_LOCK_STATS":
            # Show lock statistics across all users
            db = SessionLocal()
            try:
                # Get statistics on locked vs unlocked units
                total_progress_records = db.query(ProgressModel).count()
                
                # Count units by completion status
                locked_units = db.query(ProgressModel).filter(ProgressModel.completion_percent < 80).count()
                unlocked_units = db.query(ProgressModel).filter(ProgressModel.completion_percent >= 80).count()
                
                # Get phase distribution
                baseline_count = db.query(ProgressModel).filter(ProgressModel.current_phase == "BASELINE").count()
                balanced_count = db.query(ProgressModel).filter(ProgressModel.current_phase == "BALANCED").count()
                exam_count = db.query(ProgressModel).filter(ProgressModel.current_phase == "EXAM_BIASED").count()
                
                # Get subject breakdown
                subject_stats = db.query(
                    ProgressModel.subject,
                    func.count(ProgressModel.id).label('total'),
                    func.avg(ProgressModel.completion_percent).label('avg_completion')
                ).group_by(ProgressModel.subject).all()
                
                # Get grade breakdown
                grade_stats = db.query(
                    ProgressModel.grade,
                    func.count(ProgressModel.id).label('total'),
                    func.avg(ProgressModel.completion_percent).label('avg_completion')
                ).group_by(ProgressModel.grade).all()
                
                msg = f"""📊 *Lock Statistics Report*

━━━━━━━━━━━━━━
📈 *Overall Status*
━━━━━━━━━━━━━━

• Total Progress Records: {total_progress_records}
• Unlocked Units (80%+): {unlocked_units}
• Locked Units (<80%): {locked_units}
• Unlock Rate: {(unlocked_units/total_progress_records*100) if total_progress_records > 0 else 0:.1f}%

━━━━━━━━━━━━━━
🎯 *Phase Distribution*
━━━━━━━━━━━━━━

• Baseline Phase: {baseline_count} ({(baseline_count/total_progress_records*100) if total_progress_records > 0 else 0:.1f}%)
• Balanced Phase: {balanced_count} ({(balanced_count/total_progress_records*100) if total_progress_records > 0 else 0:.1f}%)
• Exam Biased Phase: {exam_count} ({(exam_count/total_progress_records*100) if total_progress_records > 0 else 0:.1f}%)

━━━━━━━━━━━━━━
📚 *Subject Breakdown*
━━━━━━━━━━━━━━

"""
                for subj, total, avg_comp in subject_stats:
                    msg += f"• {subj}: {total} units | Avg: {avg_comp:.1f}%\n"
                
                msg += f"""
━━━━━━━━━━━━━━
🎓 *Grade Breakdown*
━━━━━━━━━━━━━━

"""
                for grade, total, avg_comp in grade_stats:
                    msg += f"• Grade {grade}: {total} units | Avg: {avg_comp:.1f}%\n"
                
                msg += f"""
━━━━━━━━━━━━━━
ℹ️ *Lock System Info*
━━━━━━━━━━━━━━

• Unlock Threshold: 80% accuracy
• Lock Type: Sequential (per subject)
• Override: Not available (automatic only)

Last Updated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC"""
                
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Refresh", callback_data="ACT|ADMIN|VIEW_LOCK_STATS")],
                    [InlineKeyboardButton("🔙 Back to Lock Registry", callback_data="NAV|SCR_ADMIN_LOCKS|BACK")]
                ])
                
                bot.send_message(chat_id=telegram_id, text=msg, reply_markup=kb, parse_mode="Markdown")
                
            except Exception as e:
                query.answer(f"❌ Error: {str(e)}", show_alert=True)
                print(f"[ADMIN] Lock Stats Error: {e}")
            finally:
                db.close()
                

        else:
            query.answer(f"Admin Action: {param}", show_alert=True)

    elif screen == "LOCK":
        # Lock/Unlock system controls
        db = SessionLocal()
        try:
            if param.startswith("TOGGLE_FEATURE|"):
                feature_name = param.split("|")[1]
                
                # Get or create lock record
                lock = db.query(SystemLock).filter(
                    SystemLock.lock_type == "FEATURE",
                    SystemLock.lock_target == feature_name
                ).first()
                
                if not lock:
                    lock = SystemLock(
                        lock_type="FEATURE",
                        lock_target=feature_name,
                        is_locked=True,
                        locked_by=telegram_id
                    )
                    db.add(lock)
                    action_text = f"🔒 Locked feature: {feature_name}"
                else:
                    lock.is_locked = not lock.is_locked
                    lock.locked_by = telegram_id
                    lock.locked_at = dt_lib.datetime.utcnow()
                    action_text = f"{'🔒 Locked' if lock.is_locked else '🔓 Unlocked'} feature: {feature_name}"
                
                db.commit()
                query.answer(action_text, show_alert=True)
                navigate_to(bot, telegram_id, "SCR_LOCK_FEATURES", add_to_stack=False)
                
            # TOGGLE_GRADE removed per user request
                
            elif param.startswith("TOGGLE_SUBJECT|"):
                subject_name = param.split("|")[1]
                
                lock = db.query(SystemLock).filter(
                    SystemLock.lock_type == "SUBJECT",
                    SystemLock.lock_target == subject_name
                ).first()
                
                if not lock:
                    lock = SystemLock(
                        lock_type="SUBJECT",
                        lock_target=subject_name,
                        is_locked=True,
                        locked_by=telegram_id
                    )
                    db.add(lock)
                    action_text = f"🔒 Locked subject: {subject_name}"
                else:
                    lock.is_locked = not lock.is_locked
                    lock.locked_by = telegram_id
                    lock.locked_at = dt_lib.datetime.utcnow()
                    action_text = f"{'🔒 Locked' if lock.is_locked else '🔓 Unlocked'} subject: {subject_name}"
                
                db.commit()
                query.answer(action_text, show_alert=True)
                
                # Extract grade to preserve view
                g_param = "9"
                if ":" in subject_name:
                    g_param = subject_name.split(":")[1]
                navigate_to(bot, telegram_id, "SCR_LOCK_SUBJECTS", param=g_param, add_to_stack=False)
                
            elif param.startswith("TOGGLE_UNIT|"):
                unit_id = param.split("|")[1]
                
                lock = db.query(SystemLock).filter(
                    SystemLock.lock_type == "UNIT",
                    SystemLock.lock_target == unit_id
                ).first()
                
                if not lock:
                    lock = SystemLock(
                        lock_type="UNIT",
                        lock_target=unit_id,
                        is_locked=True,
                        locked_by=telegram_id
                    )
                    db.add(lock)
                    action_text = f"🔒 Locked unit: {unit_id}"
                else:
                    lock.is_locked = not lock.is_locked
                    lock.locked_by = telegram_id
                    lock.locked_at = dt_lib.datetime.utcnow()
                    action_text = f"{'🔒 Locked' if lock.is_locked else '🔓 Unlocked'} unit: {unit_id}"
                
                db.commit()
                query.answer(action_text, show_alert=True)
                # Navigate back to the unit list with proper context
                # Extract subject and grade from unit_id (e.g., "BIO_G12_U1" -> BIO, 12)
                parts = unit_id.split("_")
                if len(parts) >= 2:
                    subject = parts[0]
                    grade = parts[1].replace("G", "")
                    navigate_to(bot, telegram_id, "SCR_LOCK_UNIT_LIST", param=f"{subject}:{grade}", add_to_stack=False)
                else:
                    navigate_to(bot, telegram_id, "SCR_LOCK_UNITS", add_to_stack=False)
            
            else:
                query.answer(f"Lock Action: {param}", show_alert=True)
                
        except Exception as e:
            db.rollback()
            query.answer(f"❌ Error: {str(e)}", show_alert=True)
            print(f"[LOCK] Error: {e}")
        finally:
            db.close()


    elif screen == "REPORT_OPTIONS":
        # Blueprint Nav: ACT|REPORT_OPTIONS|TECH
        user = get_or_create_user(telegram_id, None, "User")
        session = get_or_create_session(user.id)
        if session.quiz_state:
            qs = json.loads(session.quiz_state)
            q_list = qs.get("questions", [])
            idx = qs.get("current_index", 0)
            if idx < len(q_list):
                q = q_list[idx]
                q_id = q.get("question_id")
                if q_id:
                    flag_question(q_id, param)
                    query.answer(f"🚩 Report logged for {q_id}: {param}", show_alert=True)
                else:
                    query.answer("Error: question_id missing.", show_alert=True)
            else:
                query.answer("Error: no question in state.", show_alert=True)
        else:
             query.answer("Error: quiz state lost.", show_alert=True)
        
        next_question(bot, telegram_id)
    else:
        query.answer(f"Action: {screen}|{param}")


def handle_answer(bot, query, screen, param):
    """Handle ANS actions (quiz answers)"""
    telegram_id = query.from_user.id
    
    # For Module 4, implement quiz logic
    handle_answer_selection(bot, telegram_id, param)
