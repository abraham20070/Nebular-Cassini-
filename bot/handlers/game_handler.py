"""
Game mode handler - Logic for Speed Run, Survival, and Multiplayer
"""
import json
import random
import time
from database.crud import (
    get_or_create_user, get_or_create_session, 
    update_session_state, add_xp
)
from utils.question_engine import QuestionEngine
from handlers.screen_renderer import render_screen
from handlers.navigation import navigate_to

# Time Tracking for Jobs
_ACTIVE_SPEEDRUNS = {} # {telegram_id: job}

def start_speedrun(bot, telegram_id, duration_seconds, subject_code=None, count=20, grade=None):
    """Starts a Speed Run session with a fixed timer."""
    user = get_or_create_user(telegram_id, None, "User")
    active_grade = grade if grade else user.current_grade
    
    subj_map = {"BIO": "Biology", "CHEM": "Chemistry", "PHYS": "Physics", "MATH": "Mathematics", "MIXED": None}
    subject = subj_map.get(subject_code)
    
    # Load questions from active grade
    questions = _get_random_questions(active_grade, subject=subject, count=count)
    
    if not questions:
        subj_name = subject if subject else "Mixed Science"
        bot.send_message(chat_id=telegram_id, text=f"❌ No questions available for {subj_name} Grade {active_grade} right now.")
        return

    quiz_state = {
        "mode": "SPEEDRUN",
        "duration": duration_seconds,
        "subject_code": subject_code,
        "count": count,
        "start_time": time.time(),
        "subject": subject or "Mixed Science",
        "grade": f"Grade {active_grade}",
        "unit": "Sprint" if duration_seconds <= 300 else "Exam Mode",
        "unit_id": f"SR_{duration_seconds}_{int(time.time())}",
        "questions": questions,
        "current_index": 0,
        "score": 0,
        "history": [],
        "last_feedback": "🎮"
    }
    
    update_session_state(user.id, screen="SCR_GAME_PRES", quiz_state=quiz_state)
    present_game_question(bot, telegram_id)

def start_survival(bot, telegram_id, subject_code, grade=None):
    """Starts a Survival session - ends on first mistake."""
    user = get_or_create_user(telegram_id, None, "User")
    active_grade = grade if grade else user.current_grade
    subj_map = {"BIO": "Biology", "CHEM": "Chemistry", "PHYS": "Physics", "MATH": "Mathematics"}
    subject = subj_map.get(subject_code, subject_code)
    
    questions = _get_random_questions(active_grade, subject=subject, count=100)
    
    if not questions:
        bot.send_message(chat_id=telegram_id, text=f"❌ No questions available for {subject} Grade {active_grade} right now.")
        return

    quiz_state = {
        "mode": "SURVIVAL",
        "subject": subject,
        "grade": f"Grade {active_grade}",
        "unit": "Survival Mode",
        "unit_id": f"SURVIVAL_{subject_code}_{int(time.time())}",
        "questions": questions,
        "current_index": 0,
        "score": 0,
        "history": [],
        "last_feedback": "🎮"
    }
    
    update_session_state(user.id, screen="SCR_GAME_PRES", quiz_state=quiz_state)
    present_game_question(bot, telegram_id)

def _escape_markdown(text):
    """Helper to escape common markdown characters that break rendering in legacy Markdown."""
    if not isinstance(text, str): return str(text)
    # Legacy Markdown only really cares about *, _, `, [
    return text.replace("*", "\\*").replace("_", "\\_").replace("`", "\\`").replace("[", "\\[")

def present_game_question(bot, telegram_id):
    """Displays the current question for the active session."""
    user = get_or_create_user(telegram_id, None, "User")
    session = get_or_create_session(user.id)
    state = json.loads(session.quiz_state) if session.quiz_state else {}
    
    if not state: 
        print(f"[GAME] No state found for {telegram_id}")
        return

    # SPEEDRUN Auto-End Check
    if state["mode"] == "SPEEDRUN":
        elapsed = time.time() - state["start_time"]
        if elapsed > state["duration"]:
            print(f"[GAME] Time expired ({elapsed:.1f}s > {state['duration']}s)")
            return show_game_summary(bot, telegram_id, "⏱️ Time's up!")

    idx = state["current_index"]
    if idx >= len(state["questions"]):
        print(f"[GAME] Index {idx} out of range ({len(state['questions'])})")
        return show_game_summary(bot, telegram_id, "🏆 All Questions Completed!")

    q = state["questions"][idx]
    
    # Text formatting with escaping
    q_text = _escape_markdown(q.get('question', ''))
    options = q.get("options", {})
    options_parts = []
    # Force sort A, B, C, D
    for opt in ["A", "B", "C", "D"]:
        if opt in options:
            opt_text = _escape_markdown(options[opt])
            options_parts.append(f"*{opt})* {opt_text}")
    
    question_display = f"{q_text}\n\n" + "\n\n".join(options_parts)
    
    # Header logic with feedback
    last_fb = state.get("last_feedback", "🎮")
    total = len(state["questions"])
    
    if state["mode"] == "SPEEDRUN":
        elapsed = time.time() - state["start_time"]
        remaining = int(state["duration"] - elapsed)
        if remaining < 0: remaining = 0
        
        # Format MM:SS
        mins = remaining // 60
        secs = remaining % 60
        time_str = f"{mins:02d}:{secs:02d}"
        
        # Determine Title
        mode_title = "⚡ Sprint" if state["duration"] <= 300 else "🎓 Exam Mode"
        
        # Visual Time Bar [||||||....]
        total_time = state["duration"]
        percent_left = remaining / total_time
        bar_slots = 10
        filled = int(percent_left * bar_slots)
        time_bar = "▓" * filled + "░" * (bar_slots - filled)
        
        header_pre = f"{mode_title} {last_fb} | ⏱️ {time_str}\n{time_bar}\n🏆 {state['score']} | {idx+1}/{total} Qs"
        dot_bar = "" # Handled in header_pre
    elif state["mode"] == "SURVIVAL":
        header_pre = f"🔥 {last_fb} | ❤️ 1 Life\n🏆 {state['score']} Streak"
        dot_bar = "━━━━━━━━━━━━━━"
    else:
        # Challenge Mode
        header_pre = f"⚔️ {last_fb} | 🏆 {state['score']} | {idx+1}/{total} Qs"
        dot_bar = "━━━━━━━━━━━━━━"

    # Escape the header as well just in case feedback or score has weird chars
    header_escaped = _escape_markdown(header_pre)

    extra_vars = {
        "unit_title": header_escaped,
        "dot_progress_bar": dot_bar,
        "question_stem": question_display
    }
    
    # Use SCR_GAME_PRES instead of SCR_QUIZ_PRES to remove Skip/Pin buttons
    print(f"[GAME] Rendering {state['mode']} for {telegram_id}")
    msg = render_screen(bot, user.id, telegram_id, "SCR_GAME_PRES", session.last_message_id, extra_vars)
    if msg:
        update_session_state(user.id, message_id=msg.message_id)

def handle_game_answer(bot, telegram_id, selected_opt):
    """Processes user answer during game modes."""
    user = get_or_create_user(telegram_id, None, "User")
    session = get_or_create_session(user.id)
    state = json.loads(session.quiz_state) if session.quiz_state else {}
    
    if not state: return

    # SPEEDRUN Time Check
    if state["mode"] == "SPEEDRUN":
        elapsed = time.time() - state["start_time"]
        if elapsed > state["duration"] + 2: # 2s grace for network lag
            return show_game_summary(bot, telegram_id, "⏱️ Time's Up!")

    q = state["questions"][state["current_index"]]
    
    # Question can have 'correct_answer' or 'answer' key depending on JSON source
    correct_opt = q.get("correct_answer") or q.get("answer")
    is_correct = (selected_opt == correct_opt)
    
    if is_correct:
        state["score"] += 1
        state["last_feedback"] = "✅"
        xp = 20 if state["mode"] == "SURVIVAL" else 15
        add_xp(user.id, xp)
        state["current_index"] += 1
        
        if state["current_index"] >= len(state["questions"]):
            return show_game_summary(bot, telegram_id, "🏆 All Questions Completed!")
             
        update_session_state(user.id, quiz_state=state)
        present_game_question(bot, telegram_id)
    else:
        state["last_feedback"] = "❌"
        if state["mode"] == "SURVIVAL":
            # [FIX] Provide context on why they died
            expl = q.get("explanation", "No explanation available.").strip()
            death_reason = f"💔 *Game Over!*\n\nWrong Answer! Correct was *{correct_opt}*.\n\n📖 __{expl}__"
            return show_game_summary(bot, telegram_id, death_reason)
        
        # Challenge or Speedrun: Move to next question anyway
        state["current_index"] += 1
        if state["current_index"] >= len(state["questions"]):
            reason = "🏁 Shared Practice Completed!" if state["mode"] == "CHALLENGE" else ("🎓 Timed Assessment Completed!" if state["duration"] > 300 else "⚡ Fast-Paced Practice Completed!")
            return show_game_summary(bot, telegram_id, reason)
             
        update_session_state(user.id, quiz_state=state)
        present_game_question(bot, telegram_id)

def show_game_summary(bot, telegram_id, reason):
    """Shows end-of-game stats with persistent buttons."""
    user = get_or_create_user(telegram_id, None, "User")
    session = get_or_create_session(user.id)
    state = json.loads(session.quiz_state) if session.quiz_state else {}
    
    if not state: return

    xp_earned = state['score'] * (20 if state['mode'] == 'SURVIVAL' else 15)
    
    extra_vars = {
        "game_mode": "Peer Duel" if state["mode"] == "CHALLENGE" else (state["mode"] if state["mode"] != "SPEEDRUN" else ("Exam Mode" if state.get("duration", 0) > 300 else "Sprint")),
        "reason": reason,
        "score": state["score"],
        "xp_earned": xp_earned
    }
    
    # Handle injected high score text if we implemented it, otherwise empty
    extra_vars["high_score_text"] = ""
    
    # Cancel background job if active
    if telegram_id in _ACTIVE_SPEEDRUNS:
        try:
            _ACTIVE_SPEEDRUNS[telegram_id].schedule_removal()
            del _ACTIVE_SPEEDRUNS[telegram_id]
            print(f"[JOB] Cancelled speedrun task for {telegram_id}")
        except: pass

    msg = render_screen(bot, user.id, telegram_id, "SCR_GAME_SUM", session.last_message_id, extra_vars)
    if msg:
         update_session_state(user.id, message_id=msg.message_id)

def _get_random_questions(grade, subject=None, count=20):
    """Utility to pull random questions from the data folder, respecting locks."""
    from database.models import SystemLock
    from database.crud import SessionLocal
    db = SessionLocal()
    
    # 1. Fetch active locks for this grade
    grade_locks = db.query(SystemLock).filter(
        SystemLock.is_locked == True
    ).all()
    
    locked_subjects = [l.lock_target.split(":")[0] for l in grade_locks if l.lock_type == "SUBJECT" and f":{grade}" in l.lock_target]
    locked_units = [l.lock_target for l in grade_locks if l.lock_type == "UNIT"]
    db.close()

    all_qs = []
    subjects = [subject] if subject else ["Biology", "Chemistry", "Physics", "Mathematics"]
    print(f"[RAND] Pulling questions for Grade {grade}, Subj={subject}")
    
    for s in subjects:
        # Skip locked subjects
        if s in locked_subjects:
            print(f"[RAND] Skipping locked subject: {s}")
            continue
            
        try:
            grade_str = f"Grade {grade}"
            units = QuestionEngine.list_units(s, grade_str)
            if not units:
                continue
                
            for u in units:
                unit_num = u.split(" ")[1] if " " in u else u
                # Normalize unit_id for lock check (e.g., BIO_G9_U1)
                sub_code = {"Biology": "BIO", "Chemistry": "CHEM", "Physics": "PHYS", "Mathematics": "MATH"}.get(s, s)
                unit_id = f"{sub_code}_G{grade}_U{unit_num}"
                
                # Skip locked units
                if unit_id in locked_units:
                    print(f"[RAND] Skipping locked unit: {unit_id}")
                    continue
                    
                qs, _, _ = QuestionEngine.load_unit_questions(s, grade_str, u)
                if qs: 
                    all_qs.extend(qs)
        except Exception as e:
            print(f"[RAND] Error in {s}: {e}")
            
    if not all_qs: 
        print(f"[RAND] TOTAL FAILURE: No questions found or all are locked.")
        return []
    
    random.shuffle(all_qs)
    print(f"[RAND] Success! Loaded {len(all_qs)} questions (Filtered).")
    return all_qs[:count]

def start_multiplayer_generation(bot, telegram_id, subject_code):
    """Generates a 10-question challenge and saves it to the DB."""
    user = get_or_create_user(telegram_id, None, "User")
    subj_map = {"BIO": "Biology", "CHEM": "Chemistry", "PHYS": "Physics", "MATH": "Mathematics", "MIXED": None}
    subject = subj_map.get(subject_code)
    
    questions = _get_random_questions(user.current_grade, subject=subject, count=10)
    
    if not questions:
        bot.send_message(chat_id=telegram_id, text="❌ No questions found for this subject/grade combination.")
        return

    from database.crud import create_challenge
    challenge = create_challenge(user.id, subject, user.current_grade, questions)
    
    extra_vars = {
        "challenge_id": challenge.challenge_id,
        "subject": subject or "Mixed Science"
    }
    
    # Save the challenge ID in both current_param and quiz_state for reliability
    print(f"DEBUG: Generated challenge {challenge.challenge_id}")
    update_session_state(user.id, screen="SCR_MP_LINK_READY", current_param=challenge.challenge_id, quiz_state={"unit_id": challenge.challenge_id})
    
    session = get_or_create_session(user.id)
    render_screen(bot, user.id, telegram_id, "SCR_MP_LINK_READY", session.last_message_id, extra_vars)

def handle_mp_share(bot, telegram_id):
    """Provides a copyable deep link for the challenge."""
    user = get_or_create_user(telegram_id, None, "User")
    session = get_or_create_session(user.id)
    
    # Retrieve challenge ID from current_param or quiz_state
    challenge_id = session.current_param
    if not challenge_id or not str(challenge_id).startswith("CH_"):
        state = json.loads(session.quiz_state) if session.quiz_state else {}
        challenge_id = state.get("unit_id")
    
    if not challenge_id:
        print(f"ERROR: No challenge ID found in session for {telegram_id}")
        bot.send_message(chat_id=telegram_id, text="❌ Error: Challenge context lost. Please create a new one.")
        return
        
    bot_username = bot.get_me().username
    deep_link = f"https://t.me/{bot_username}?start={challenge_id}"
    
    share_text = f"🔥 <b>Challenge your friends!</b>\n\nI just generated a Science quiz challenge for Grade {user.current_grade}. Can you beat me?\n\n🔗 {deep_link}"
    
    print(f"DEBUG: Sharing link {challenge_id}")
    bot.send_message(chat_id=telegram_id, text=share_text, parse_mode="HTML")

def start_challenge_session(bot, telegram_id, challenge):
    """Starts a challenge session for a recipient."""
    user = get_or_create_user(telegram_id, None, "User")
    import json
    questions = json.loads(challenge["questions_json"])
    
    quiz_state = {
        "mode": "CHALLENGE",
        "subject": challenge.get("subject") or "Mixed Science",
        "grade": f"Grade {challenge['grade']}",
        "unit": "Multiplayer Challenge",
        "unit_id": challenge["challenge_id"],
        "questions": questions,
        "current_index": 0,
        "score": 0,
        "history": [],
        "last_feedback": "🎮"
    }
    
    update_session_state(user.id, quiz_state=quiz_state)
    present_game_question(bot, telegram_id)
