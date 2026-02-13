import json
import time
import random
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from database.crud import (
    get_or_create_user, get_or_create_session, 
    update_session_state, update_user_streak, add_xp,
    update_phase_progress, add_to_review_queue, remove_from_review_queue,
    get_review_queue_items
)
from utils.question_engine import QuestionEngine
from handlers.screen_renderer import render_screen
from handlers.navigation import navigate_to
from database.db import SessionLocal
from database.models import SystemLock
import handlers.game_handler as gh

def start_quiz_session(bot, telegram_id, subject_code, grade, unit):
    """
    Initializes a new quiz session by loading a batch of questions.
    """
    # Mapping for shorthand codes to folder names
    subject_map = {
        "BIO": "Biology",
        "CHEM": "Chemistry",
        "PHYS": "Physics",
        "MATH": "Mathematics"
    }
    subject = subject_map.get(subject_code, subject_code)
    
    # Get user with all required fields
    user = get_or_create_user(telegram_id, None, "User")
    session = get_or_create_session(user.id)
    
    # Unit ID for database (e.g. BIO_G10_U1)
    # Unit label is "Unit 1", we want "U1"
    unit_num = unit.split(" ")[1] if " " in unit else "1"
    unit_id = f"{subject_code}_{grade.replace(' ', '')}_U{unit_num}"
    
    # Load ALL available questions for the unit
    questions, state, full_unit_title = QuestionEngine.load_unit_questions(subject, grade, unit)
    
    if not questions:
        bot.send_message(chat_id=telegram_id, text=f"Error: Could not load questions for {subject} {grade} {unit}.")
        return

    # [FIX] Inject source_unit for better tracking in Review Queue
    for q in questions:
        q["source_unit"] = unit

    # Initialize quiz state in session
    quiz_state = {
        "subject_code": subject_code,
        "subject": subject,
        "grade": grade,
        "unit": unit,
        "unit_title": full_unit_title or unit, 
        "unit_id": unit_id,
        "questions": questions,
        "current_index": 0,
        "score": 0,
        "history": []
    }
    
    update_session_state(user.id, quiz_state=quiz_state)
    
    # Show first question
    present_question(bot, telegram_id)

def present_question(bot, telegram_id):
    """
    Renders the current question from the session state.
    """
    user = get_or_create_user(telegram_id, None, "User")
    session = get_or_create_session(user.id)
    quiz_state = json.loads(session.quiz_state) if session.quiz_state else {}
    
    if not quiz_state or "questions" not in quiz_state:
        return navigate_to(bot, telegram_id, "SCR_HUB")

    idx = quiz_state["current_index"]
    questions = quiz_state["questions"]
    
    if idx >= len(questions):
        return show_quiz_summary(bot, telegram_id)

    q = questions[idx]
    
    # Prepare variables for renderer (Match blueprint SCR_QUIZ_PRES)
    # Extract just the number from "Grade X" if possible
    grade_num = quiz_state["grade"].split(" ")[1] if " " in quiz_state["grade"] else quiz_state["grade"]
    
    # Construct question body with options
    q_text = q["question"]
    opts = q.get("options", {})
    options_parts = []
    for key in ["A", "B", "C", "D"]:
        if key in opts:
            options_parts.append(f"{key}) {opts[key]}")
    
    # User wanted NO leading dots/indentation for the stem or options
    question_display = f"{q_text}\n\n" + "\n\n".join(options_parts)

    # Scale progress bar: If 20 or fewer questions, show 1 dot per question. 
    # If more, scale to a fixed 10 dots to prevent screen overflow.
    total = len(questions)
    bar_length = total if total <= 20 else 10
    
    filled = int(((idx + 1) / total) * bar_length)
    empty = bar_length - filled
    dot_bar = "●" * filled + "○" * empty

    # If it's a Random Quiz, we want to override the header to avoid "Mixed Science - Grade 10" confusion
    # Blueprint: "📖 {subject} - Grade {grade}              {curr_index}/{total_count}\n{unit_title}"
    
    display_subject = quiz_state["subject"]
    if quiz_state.get("unit") == "Random Quiz":
        display_subject = "Random Quiz"
    
    extra_vars = {
        "subject": display_subject,
        "grade": grade_num,
        "unit_title": quiz_state.get("unit_title", quiz_state["unit"]),
        "dot_progress_bar": dot_bar,
        "curr_index": idx + 1,
        "total_count": total,
        "unit_progress": int((idx / total) * 100),
        "question_stem": question_display,
        "has_formula": q.get("formula") is not None
    }
    
    # Inject option variables for potential dynamic row mapping
    for key in ["A", "B", "C", "D"]:
        extra_vars[f"opt_{key.lower()}"] = opts.get(key, "")
    
    # Use different screen for Game Modes if presentation differs
    target_screen = "SCR_QUIZ_PRES"
    if quiz_state.get("mode") == "CHALLENGE":
        target_screen = "SCR_GAME_PRES"
        # Match Game Mode Header Style: "⚔️ {last_fb} | 🏆 {score} | {idx+1}/{total} Qs"
        last_fb = quiz_state.get("last_feedback", "🎮")
        extra_vars["unit_title"] = f"⚔️ {last_fb} | 🏆 {quiz_state['score']} | {idx+1}/{total} Qs"
        extra_vars["dot_progress_bar"] = "━━━━━━━━━━━━━━"
        
    msg = render_screen(bot, user.id, telegram_id, target_screen, session.last_message_id, extra_vars)
    
    # [FIX] If Random Quiz, we might need to update the Quit button action in the cache-rendered message
    # BUT render_screen generally handles the layout. The Action "NAV|SCR_UNITS|BACK" is in the blueprint.
    # To fix this properly without modifying blueprint cache globally, we should intercept the Quit navigation in callback_router.
    # OR better: The "Action" is just a string in the callback. We can't easily change the button text action dynamically in render_screen easily without complex logic.
    # Strategy: Let's rely on callback_router to handle "SCR_UNITS|BACK" intelligently.
    
    if msg:
        update_session_state(user.id, message_id=msg.message_id)

def handle_answer_selection(bot, telegram_id, selected_opt):
    """
    Processes the user's answer selection.
    """
    user = get_or_create_user(telegram_id, None, "User")
    session = get_or_create_session(user.id)
    quiz_state = json.loads(session.quiz_state) if session.quiz_state else {}
    
    if not quiz_state: return
    
    # Check if this is a Game Mode session (Speedrun/Survival stay fast without feedback)
    if quiz_state.get("mode") in ["SPEEDRUN", "SURVIVAL"]:
        return gh.handle_game_answer(bot, telegram_id, selected_opt)

    idx = quiz_state["current_index"]
    q = quiz_state["questions"][idx]
    correct_opt = q["correct_answer"]
    
    is_correct = (selected_opt == correct_opt)
    
    # [FIX] Record attempt in database for progress tracking
    from database.crud import record_quiz_attempt
    grade_val = 9
    try:
        g_raw = str(quiz_state["grade"])
        grade_val = int(g_raw.split(" ")[1]) if " " in g_raw else int(g_raw)
    except: pass
    
    record_quiz_attempt(
        user.id,
        q.get("source_unit", quiz_state.get("unit_id", "UNKNOWN")),
        quiz_state["subject"],
        grade_val,
        is_correct
    )

    if is_correct:
        quiz_state["score"] += 1
        add_xp(user.id, 10) # 10 XP per correct answer
        
        # Remove from review queue if it exists (Mastery achieved)
        remove_from_review_queue(user.id, q["question_id"])
    else:
        # Add to review queue as MISTAKE
        # Add to review queue as MISTAKE
        add_to_review_queue(
            user_id=user.id,
            question_id=q["question_id"],
            status="MISTAKE",
            subject=quiz_state["subject"],
            grade=int(quiz_state["grade"].split(" ")[1]) if " " in str(quiz_state["grade"]) else int(quiz_state["grade"]) if str(quiz_state["grade"]).isdigit() else 9,
            unit=q.get("source_unit", quiz_state["unit"])
        )
    
    quiz_state["history"].append({
        "q_id": q.get("question_id", f"Q_{idx}"),
        "selected": selected_opt,
        "correct": correct_opt,
        "is_correct": is_correct
    })
    
    update_session_state(user.id, quiz_state=quiz_state)
    
    # Status should be ONLY one:
    status_text = "✅ correct" if is_correct else "❌ incorrect"
    
    # Textbook explanation with NO indentation
    explanation_body = q.get("explanation", "No explanation available.").strip()

    # Re-construct question display for context
    q_text = q.get("question", "")
    opts = q.get("options", {})
    options_parts = []
    for key in ["A", "B", "C", "D"]:
        if key in opts:
            options_parts.append(f"{key}) {opts[key]}")
    question_display = f"{q_text}\n\n" + "\n\n".join(options_parts)
    
    # Extract grade number for consistent parsing if needed elsewhere
    grade_val = quiz_state.get("grade", "9").replace("Grade", "").strip()

    extra_vars = {
        "unit_title": quiz_state.get("unit_title", quiz_state["unit"]),
        "status_text": status_text,
        "correct_option_letter": correct_opt,
        "explanation_text": explanation_body,
        "ai_explanation": "",
        "question_stem": question_display,
        "grade": grade_val
    }

    if quiz_state.get("mode") == "CHALLENGE":
        # Capture feedback for the next question header
        quiz_state["last_feedback"] = "✅" if is_correct else "❌"
        update_session_state(user.id, quiz_state=quiz_state)
        # Update current feedback header
        extra_vars["unit_title"] = f"⚔️ {quiz_state['last_feedback']} | 🏆 {quiz_state['score']} | {idx+1}/{len(quiz_state['questions'])} Qs"
    
    msg = render_screen(bot, user.id, telegram_id, "SCR_QUIZ_FB", session.last_message_id, extra_vars)
    if msg:
        update_session_state(user.id, message_id=msg.message_id)

def skip_question(bot, telegram_id):
    """Marks current question as skipped and moves to next."""
    user = get_or_create_user(telegram_id, None, "User")
    session = get_or_create_session(user.id)
    quiz_state = json.loads(session.quiz_state) if session.quiz_state else {}
    if not quiz_state: return
    
    idx = quiz_state["current_index"]
    
    # [FIX] Crash protection: Ensure index is valid
    if idx >= len(quiz_state["questions"]):
        print(f"[SKIP] Index {idx} out of range (max {len(quiz_state['questions'])})")
        return show_quiz_summary(bot, telegram_id)
        
    q = quiz_state["questions"][idx]
    
    quiz_state["history"].append({
        "q_id": q.get("question_id", f"Q_{idx}"),
        "selected": "SKIP",
        "correct": q["correct_answer"],
        "is_correct": False
    })

    # Track as SKIPPED in review queue
    add_to_review_queue(
        user_id=user.id,
        question_id=q["question_id"],
        status="SKIPPED",
        subject=quiz_state["subject"],
        grade=int(quiz_state["grade"].split(" ")[1]) if " " in str(quiz_state["grade"]) else int(quiz_state["grade"]) if str(quiz_state["grade"]).isdigit() else 9,
        unit=quiz_state["unit"]
    )

    quiz_state["current_index"] += 1
    update_session_state(user.id, quiz_state=quiz_state)
    present_question(bot, telegram_id)

def next_question(bot, telegram_id):
    """Transitions to the next question in the batch."""
    user = get_or_create_user(telegram_id, None, "User")
    session = get_or_create_session(user.id)
    quiz_state = json.loads(session.quiz_state) if session.quiz_state else {}
    if not quiz_state: return
    quiz_state["current_index"] += 1
    update_session_state(user.id, quiz_state=quiz_state)
    present_question(bot, telegram_id)

def show_quiz_summary(bot, telegram_id):
    """Shows the final summary screen for the quiz batch."""
    user = get_or_create_user(telegram_id, None, "User")
    session = get_or_create_session(user.id)
    quiz_state = json.loads(session.quiz_state) if session.quiz_state else {}
    if not quiz_state: return
    
    # Handle Game Mode Summaries
    if quiz_state.get("mode") == "CHALLENGE":
        return gh.show_game_summary(bot, telegram_id, "🏁 Shared Practice Completed!")

    accuracy = (quiz_state["score"] / len(quiz_state["questions"])) * 100
    
    phase_data = {
        "BASELINE": {"num": "1", "name": "Initial Assessment", "bar": "🟢🟢⚪⚪⚪⚪⚪⚪⚪⚪"},
        "BALANCED": {"num": "2", "name": "Balanced Mastery", "bar": "🟢🟢🟢🟢🟢⚪⚪⚪⚪⚪"},
        "EXAM_BIASED": {"num": "3", "name": "National Exam Preparation", "bar": "🟢🟢🟢🟢🟢🟢🟢🟢⚪⚪"}
    }
    curr_phase_str = "BASELINE"
    if accuracy >= 80: curr_phase_str = "BALANCED"
    if accuracy >= 95: curr_phase_str = "EXAM_BIASED"
    pd = phase_data[curr_phase_str]
    update_phase_progress(
        user_id=user.id, unit_id=quiz_state["unit_id"],
        subject=quiz_state["subject"], 
        grade=int(quiz_state["grade"].split(" ")[1]) if "Grade" in quiz_state["grade"] else 9, 
        phase=curr_phase_str, accuracy=accuracy
    )
    update_user_streak(user.id)
    skipped = len([h for h in quiz_state["history"] if h.get("selected") == "SKIP"])
    xp_gained = int(accuracy / 10) * 10
    
    # Extract unit number for "THE END OF UNIT X" header
    unit_str = quiz_state.get("unit", "Unit 1")
    unit_num = unit_str.split(" ")[1] if " " in unit_str else "1"

    extra_vars = {
        "unit_title": quiz_state.get("unit_title", quiz_state["unit"]),
        "unit_num": unit_num,
        "correct_count": quiz_state["score"],
        "incorrect_count": len(quiz_state["questions"]) - quiz_state["score"] - skipped,
        "skipped_count": skipped,
        "accuracy_percentage": int(accuracy),
        "streak": user.streak_count,
        "xp_reward": xp_gained,
        "curr_phase": pd["num"],
        "phase_name": pd["name"],
        "phase_bar": pd["bar"]
    }
    
    # Use different screen for Review/Random sessions
    target_screen = "SCR_QUIZ_SUM"
    if "_REV_" in quiz_state.get("unit_id", "") or "SMART" in quiz_state.get("unit_id", ""):
        target_screen = "SCR_REVIEW_SUM"
    elif quiz_state.get("unit") == "Random Quiz":
        target_screen = "SCR_RANDOM_SUM"
        # Ensure 'grade' is just the number for the button param
        extra_vars["grade"] = str(quiz_state.get("grade", "9")).replace("Grade", "").strip()
        
    render_screen(bot, user.id, telegram_id, target_screen, session.last_message_id, extra_vars)

def start_next_part(bot, telegram_id):
    """Transitions to the next review part."""
    user = get_or_create_user(telegram_id, None, "User")
    session = get_or_create_session(user.id)
    quiz_state = json.loads(session.quiz_state) if session.quiz_state else {}
    if not quiz_state: return
    
    uid = quiz_state.get("unit_id", "")
    if "_REV_P" in uid:
        part_num = int(uid.split("_REV_P")[-1])
        if part_num < 3:
            return start_review_session(bot, telegram_id, quiz_state["subject_code"], quiz_state["grade"], section_num=part_num+1)
            
    # If no next part, go to review hub
    from handlers.navigation import navigate_to
    navigate_to(bot, telegram_id, "SCR_REVIEW_HUB", add_to_stack=False)

def replay_batch(bot, telegram_id):
    """Resets the current quiz session to the beginning."""
    user = get_or_create_user(telegram_id, None, "User")
    session = get_or_create_session(user.id)
    quiz_state = json.loads(session.quiz_state) if session.quiz_state else {}
    if not quiz_state: return
    
    quiz_state["current_index"] = 0
    quiz_state["score"] = 0
    quiz_state["history"] = []
    update_session_state(user.id, quiz_state=quiz_state)
    present_question(bot, telegram_id)

def start_next_batch(bot, telegram_id):
    """Loads the next unit or round for the user."""
    user = get_or_create_user(telegram_id, None, "User")
    session = get_or_create_session(user.id)
    quiz_state = json.loads(session.quiz_state) if session.quiz_state else {}
    if not quiz_state: return
    
    subject = quiz_state["subject"]
    subject_code = quiz_state["subject_code"]
    grade = quiz_state["grade"]
    unit = quiz_state["unit"]
    
    # Logic to find next unit
    units = QuestionEngine.list_units(subject, grade)
    try:
        curr_idx = units.index(unit)
        if curr_idx + 1 < len(units):
            next_u = units[curr_idx + 1]
            return start_quiz_session(bot, telegram_id, subject_code, grade, next_u)
    except ValueError:
        pass
    
    # If no next unit, go back to units
    navigate_to(bot, telegram_id, "SCR_UNITS", param=f"{subject_code}:{grade}")

def start_review_session(bot, telegram_id, subject_code, grade, section_num=1):
    """
    Initializes a review session by loading questions from a subset of units (1/3 of the total).
    The questions within the section are randomized.
    """
    subject_map = {"BIO": "Biology", "CHEM": "Chemistry", "PHYS": "Physics", "MATH": "Mathematics"}
    subject = subject_map.get(subject_code, subject_code)
    user = get_or_create_user(telegram_id, None, "User")
    
    # 1. Get all units for this grade
    units = QuestionEngine.list_units(subject, grade)
    if not units:
        bot.send_message(chat_id=telegram_id, text=f"No units found for {subject} {grade}.")
        return

    # 2. Divide units into 3 sections
    import math
    num_units = len(units)
    chunk_size = math.ceil(num_units / 3)
    
    # Define start/end for the requested section
    if section_num == 1:
        target_units = units[:chunk_size]
    elif section_num == 2:
        target_units = units[chunk_size:2*chunk_size]
    else: # Section 3
        target_units = units[2*chunk_size:]
        
    if not target_units:
        bot.send_message(chat_id=telegram_id, text="No units found in this section.")
        return

    # 3. Collect questions from the targeted units (Respecting Locks)
    from database.models import SystemLock
    from database.crud import SessionLocal
    db = SessionLocal()
    
    # Fetch active unit locks for this subject/grade
    grade_val = grade.replace("Grade ", "").strip()
    unit_locks = db.query(SystemLock.lock_target).filter(
        SystemLock.lock_type == "UNIT",
        SystemLock.is_locked == True,
        SystemLock.lock_target.like(f"{subject_code}_G{grade_val}_U%")
    ).all()
    locked_unit_ids = [l[0] for l in unit_locks]
    db.close()

    all_questions = []
    for u in target_units:
        # Check if this specific unit is locked
        u_num = u.split(" ")[1] if " " in u else u
        unit_id = f"{subject_code}_G{grade_val}_U{u_num}"
        if unit_id in locked_unit_ids:
            continue

        questions, _, _ = QuestionEngine.load_unit_questions(subject, grade, u)
        if questions:
            all_questions.extend(questions)
    
    if not all_questions:
        bot.send_message(chat_id=telegram_id, text="No questions available in this section.")
        return

    # 4. Shuffle questions for a randomized experience
    import random
    random.shuffle(all_questions)
    
    quiz_state = {
        "subject_code": subject_code,
        "subject": subject,
        "grade": grade,
        "unit": f"Review Part {section_num}",
        "unit_title": f"Review {subject}: Part {section_num}", 
        "unit_id": f"{subject_code}_{grade.replace(' ', '')}_REV_P{section_num}",
        "questions": all_questions,
        "current_index": 0,
        "score": 0,
        "history": []
    }
    
    update_session_state(user.id, quiz_state=quiz_state)
    present_question(bot, telegram_id)

def show_hint(bot, telegram_id, query):
    """Displays the hint for the current question."""
    user = get_or_create_user(telegram_id, None, "User")
    session = get_or_create_session(user.id)
    quiz_state = json.loads(session.quiz_state) if session.quiz_state else {}
    if not quiz_state: return
    
    idx = quiz_state["current_index"]
    q = quiz_state["questions"][idx]
    hint = q.get("hint") or q.get("explanation", "")[:100] + "..."
    if not hint:
        hint = "Focus on the core concept of the unit."
    
    query.answer(f"💡 Hint: {hint}", show_alert=True)

def start_smart_review(bot, telegram_id, subject_code, grade, review_type):
    """
    Starts a review session based on 'SKIPPED' or 'MISTAKE' items.
    """
    subject_map = {"BIO": "Biology", "CHEM": "Chemistry", "PHYS": "Physics", "MATH": "Mathematics"}
    subject = subject_map.get(subject_code, subject_code)
    user = get_or_create_user(telegram_id, None, "User")
    
    # 1. Get items from DB
    items = get_review_queue_items(user.id, review_type, subject=subject, grade=int(grade.split(" ")[1]) if " " in grade else 9)
    if not items:
        bot.send_message(chat_id=telegram_id, text=f"You have no {review_type.lower()} questions to review for {subject} {grade}! Great job!")
        return

    # 2. Group by Unit to minimize JSON loading
    unit_map = {}
    for item in items:
        if item.unit not in unit_map:
            unit_map[item.unit] = []
        unit_map[item.unit].append(item.question_id)
        
    # 4. Load Questions Match Logic
    final_questions = []
    
    # Cache list of units to avoid repeated I/O if we need to scan
    cached_all_units = []

    for unit_name, q_ids in unit_map.items():
        # [FIX] Smart Unit Loading
        # 1. Try to load from the stored unit name first
        unit_qs, _, _ = QuestionEngine.load_unit_questions(subject, grade, unit_name)
        
        found_in_unit = False
        for q in unit_qs:
            if q.get("question_id") in q_ids:
                final_questions.append(q)
                found_in_unit = True
        
        # 2. If not found (e.g. question moved, or stored unit name was generic "Random Quiz"), SCAN ALL
        if not found_in_unit and q_ids:
             print(f"[SMART_REVIEW] Warning: Questions {q_ids} not found in '{unit_name}'. Scanning all units...")
             
             if not cached_all_units:
                 cached_all_units = QuestionEngine.list_units(subject, grade)
                 
             for u in cached_all_units:
                 if u == unit_name: continue # Already checked above
                 
                 qs, _, _ = QuestionEngine.load_unit_questions(subject, grade, u)
                 for q in qs:
                     if q.get("question_id") in q_ids:
                         final_questions.append(q)
                         # Optimization: If we found all needed for this batch, break inner loop? 
                         # No, q_ids might be scattered. Just continue.

    if not final_questions:
        bot.send_message(chat_id=telegram_id, text="Error: Could not load question data. Please contact admin.")
        return

    # 5. Shuffle
    random.shuffle(final_questions)
    
    # 5. Start Session
    quiz_state = {
        "subject_code": subject_code,
        "subject": subject,
        "grade": grade,
        "unit": f"Smart Review ({review_type})",
        "unit_title": f"Review: {review_type.title()} Questions", 
        "unit_id": f"{subject_code}_{grade.replace(' ', '')}_SMART_{review_type}",
        "questions": final_questions,
        "current_index": 0,
        "score": 0,
        "history": []
    }
    
    update_session_state(user.id, quiz_state=quiz_state)
    present_question(bot, telegram_id)

def start_random_quiz(bot, telegram_id, grade=None):
    """
    Starts a random quiz using 10 questions from the user's current grade across all subjects.
    Selections are randomized from 2 units per subject to ensure variety.
    """
    user = get_or_create_user(telegram_id, None, "User")
    # Handle case where user might not have a grade set (default to 9)
    # If grade param is provided (str or int), use it, otherwise fallback to user profile
    current_grade = user.current_grade if user.current_grade else 9
    if grade:
        try:
            # Handle inputs like "Grade 9" or just "9"
            clean_grade = str(grade).replace("Grade", "").strip()
            current_grade = int(clean_grade)
        except:
            if hasattr(user, 'current_grade'): 
                current_grade = user.current_grade
            else:
                current_grade = 9

    grade_str = f"Grade {current_grade}"
    print(f"[RAND] Starting Random Quiz for {grade_str} (Param: {grade})")
    
    # Force override default subject for Random Quiz so header doesn't show "Mixed Science"
    # The header is "📖 {subject} - Grade {grade} ..."
    final_subject = "Review" # Will appear as "Review - Grade 12"
    
    subjects = ["Biology", "Chemistry", "Physics", "Mathematics"]
    all_questions = []
    
    # 1. Gather questions from random units per subject (Respecting Locks)
    db = SessionLocal()
    locks = db.query(SystemLock).filter(SystemLock.is_locked == True).all()
    
    locked_subjects = [l.lock_target.split(":")[0] for l in locks if l.lock_type == "SUBJECT" and f":{current_grade}" in l.lock_target]
    locked_units = [l.lock_target for l in locks if l.lock_type == "UNIT"]
    db.close()
    
    for subj in subjects:
        # Skip locked subjects
        if subj in locked_subjects:
            print(f"[RANDOM] Skipping locked subject: {subj}")
            continue

        units = QuestionEngine.list_units(subj, grade_str)
        if not units: continue
            
        random.shuffle(units)
        # Select up to 2 units per subject for variety
        selected_units = units[:2]
        
        for u in selected_units:
            # Check unit lock
            u_num = u.split(" ")[1] if " " in u else u
            sub_code = {"Biology": "BIO", "Chemistry": "CHEM", "Physics": "PHYS", "Mathematics": "MATH"}.get(subj, subj)
            unit_id = f"{sub_code}_G{current_grade}_U{u_num}"
            
            if unit_id in locked_units:
                print(f"[RANDOM] Skipping locked unit: {unit_id}")
                continue

            qs, _, _ = QuestionEngine.load_unit_questions(subj, grade_str, u)
            if qs:
                # [FIX] Inject Source Unit for Smart Review
                for q in qs:
                    q["source_unit"] = u
                all_questions.extend(qs)
                
    if not all_questions:
        bot.send_message(chat_id=telegram_id, text=f"❌ No questions found for {grade_str}. Please try another grade.")
        return

    # 2. Select 10 random questions from the pool
    random.shuffle(all_questions)
    selected_questions = all_questions[:10]
    
    # 3. Initialize Session
    quiz_state = {
        "subject_code": "MIXED",
        "subject": "Review", # Shows as "📖 Review - Grade X"
        "grade": grade_str,
        "unit": "Random Quiz",
        "unit_title": f"⚡ Random Quiz (Grade {current_grade})", 
        "unit_id": f"RANDOM_{grade_str.replace(' ', '')}_{int(time.time())}",
        "questions": selected_questions,
        "current_index": 0,
        "score": 0,
        "history": []
    }
    
    update_session_state(user.id, quiz_state=quiz_state)
    present_question(bot, telegram_id)

