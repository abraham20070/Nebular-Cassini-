"""
CRUD operations for Nebular Cassini Bot
"""
from sqlalchemy.orm import Session
from sqlalchemy import and_
from datetime import datetime, timedelta
from typing import Optional, List
import json
import time

from .models import User, Progress, Session as SessionModel, FlaggedQuestion, ReviewQueue, Challenge
from .db import SessionLocal
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import PHASE_UNLOCK_THRESHOLD


# ==================== USER OPERATIONS ====================

def get_or_create_user(telegram_id: int, username: Optional[str], full_name: str) -> User:
    """Get existing user or create new one"""
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.telegram_id == telegram_id).first()
        if user:
            # Update username/name if changed
            if username and user.username != username:
                user.username = username
            if full_name and full_name != "User" and user.full_name != full_name:
                user.full_name = full_name
            db.commit()
            db.refresh(user)
            return user
        
        # Create new user
        user = User(
            telegram_id=telegram_id,
            username=username,
            full_name=full_name,
            join_date=datetime.utcnow(),
            current_grade=9,  # Default
            streak_count=0,
            last_activity=datetime.utcnow(),
            total_xp=0,
            level=1
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user
    finally:
        db.close()


def update_user_streak(user_id: int) -> int:
    """Update streak counter based on last activity. Returns new streak count."""
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return 0
        
        now = datetime.utcnow()
        last_active = user.last_activity
        time_diff = now - last_active
        
        # If less than 24 hours, just update timestamp
        if time_diff < timedelta(hours=24):
            user.last_activity = now
        # If 24-48 hours, increment streak
        elif timedelta(hours=24) <= time_diff < timedelta(hours=48):
            user.streak_count += 1
            user.last_activity = now
        # If more than 48 hours, reset streak
        else:
            user.streak_count = 1
            user.last_activity = now
        
        db.commit()
        db.refresh(user)
        return user.streak_count
    finally:
        db.close()


def add_xp(user_id: int, xp_amount: int) -> int:
    """Add XP to user and recalculate level. Returns new total XP."""
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return 0
        
        # Check if we need to reset weekly XP (Monday 00:00 UTC)
        now = datetime.utcnow()
        if user.week_start_date:
            days_since_start = (now - user.week_start_date).days
            # If it's been 7+ days, reset weekly XP
            if days_since_start >= 7:
                user.weekly_xp = 0
                user.week_start_date = now
        else:
            # Initialize week_start_date if not set
            user.week_start_date = now
        
        user.total_xp += xp_amount
        user.weekly_xp += xp_amount
        
        # Calculate level (simple formula: level = sqrt(XP / 100))
        user.level = int((user.total_xp / 100) ** 0.5) + 1
        
        db.commit()
        db.refresh(user)
        return user.total_xp
    finally:
        db.close()


def get_leaderboard(limit: int = 10) -> List[User]:
    """Get top users by XP"""
    db = SessionLocal()
    try:
        return db.query(User).order_by(User.total_xp.desc()).limit(limit).all()
    finally:
        db.close()


def get_weekly_leaderboard(limit: int = 10) -> List[User]:
    """Get top users by weekly XP"""
    db = SessionLocal()
    try:
        return db.query(User).order_by(User.weekly_xp.desc()).limit(limit).all()
    finally:
        db.close()


# ==================== PROGRESS OPERATIONS ====================

def get_user_progress(user_id: int, unit_id: str) -> Optional[Progress]:
    """Get progress record for a specific unit"""
    db = SessionLocal()
    try:
        return db.query(Progress).filter(
            and_(Progress.user_id == user_id, Progress.unit_id == unit_id)
        ).first()
    finally:
        db.close()


def update_phase_progress(
    user_id: int,
    unit_id: str,
    subject: str,
    grade: int,
    phase: str,
    accuracy: float
) -> Progress:
    """
    Update progress for a specific phase.
    Automatically unlocks next phase if accuracy >= threshold.
    """
    db = SessionLocal()
    try:
        # Get or create progress record
        progress = db.query(Progress).filter(
            and_(Progress.user_id == user_id, Progress.unit_id == unit_id)
        ).first()
        
        if not progress:
            progress = Progress(
                user_id=user_id,
                unit_id=unit_id,
                subject=subject,
                grade=grade,
                current_phase="BASELINE"
            )
            db.add(progress)
        
        # Update accuracy for the specified phase
        if phase == "BASELINE":
            progress.baseline_accuracy = accuracy
            if accuracy >= PHASE_UNLOCK_THRESHOLD:
                progress.unlocked_balanced = True
        elif phase == "BALANCED":
            progress.balanced_accuracy = accuracy
            if accuracy >= PHASE_UNLOCK_THRESHOLD:
                progress.unlocked_exam = True
        elif phase == "EXAM_BIASED":
            progress.exam_accuracy = accuracy
        
        # Update current phase to highest unlocked
        if progress.unlocked_exam:
            progress.current_phase = "EXAM_BIASED"
        elif progress.unlocked_balanced:
            progress.current_phase = "BALANCED"
        else:
            progress.current_phase = "BASELINE"
        
        db.commit()
        db.refresh(progress)
        return progress
    finally:
        db.close()


def record_quiz_attempt(
    user_id: int,
    unit_id: str,
    subject: str,
    grade: int,
    correct: bool
) -> Progress:
    """Record a single question attempt"""
    db = SessionLocal()
    try:
        progress = db.query(Progress).filter(
            and_(Progress.user_id == user_id, Progress.unit_id == unit_id)
        ).first()
        
        if not progress:
            progress = Progress(
                user_id=user_id,
                unit_id=unit_id,
                subject=subject,
                grade=grade,
                questions_attempted=0,
                questions_correct=0,
                completion_percent=0.0
            )
            db.add(progress)
        
        progress.questions_attempted += 1
        if correct:
            progress.questions_correct += 1
        
        # Recalculate completion percentage
        if progress.questions_attempted > 0:
            progress.completion_percent = (
                progress.questions_correct / progress.questions_attempted
            ) * 100
            # Ensure 0% is not treated as non-existent in UI logic if attempts > 0
            if progress.completion_percent == 0 and progress.questions_attempted > 0:
                 # It's technically 0%, but the record exists so it won't be "Not Started"
                 pass
        
        db.commit()
        db.refresh(progress)
        return progress
    finally:
        db.close()


def get_all_user_progress(user_id: int) -> List[Progress]:
    """Get all progress records for a user"""
    db = SessionLocal()
    try:
        return db.query(Progress).filter(Progress.user_id == user_id).all()
    finally:
        db.close()


# ==================== SESSION OPERATIONS ====================

def get_or_create_session(user_id: int) -> SessionModel:
    """Get or create session for user"""
    db = SessionLocal()
    try:
        session = db.query(SessionModel).filter(SessionModel.user_id == user_id).first()
        if session:
            return session
        
        session = SessionModel(
            user_id=user_id,
            current_screen="SCR_WELCOME",  # New users start at welcome
            navigation_stack="[]",
            session_active=True
        )
        db.add(session)
        db.commit()
        db.refresh(session)
        return session
    finally:
        db.close()


def update_session_state(
    user_id: int,
    screen: Optional[str] = None,
    current_param: Optional[str] = None,
    message_id: Optional[int] = None,
    quiz_state: Optional[dict] = None,
    add_to_nav_stack: bool = False
) -> SessionModel:
    """Update session state"""
    db = SessionLocal()
    try:
        session = db.query(SessionModel).filter(SessionModel.user_id == user_id).first()
        if not session:
            session = SessionModel(user_id=user_id)
            db.add(session)
        
        if screen:
            # Update navigation stack if requested
            if add_to_nav_stack and session.current_screen != screen:
                nav_stack = json.loads(session.navigation_stack)
                # Store (previous_screen, previous_param)
                nav_stack.append([session.current_screen, session.current_param])
                # Keep stack size reasonable (max 10)
                if len(nav_stack) > 10:
                    nav_stack = nav_stack[-10:]
                session.navigation_stack = json.dumps(nav_stack)
            
            session.current_screen = screen
            session.current_param = current_param
        
        if message_id is not None:
            session.last_message_id = message_id
        
        if quiz_state is not None:
            session.quiz_state = json.dumps(quiz_state)
        
        session.updated_at = datetime.utcnow()
        
        db.commit()
        db.refresh(session)
        return session
    finally:
        db.close()


def pop_navigation_stack(user_id: int) -> Optional[tuple]:
    """Pop last screen from navigation stack. Returns (screen, param) or None."""
    db = SessionLocal()
    try:
        session = db.query(SessionModel).filter(SessionModel.user_id == user_id).first()
        if not session:
            return None
        
        nav_stack = json.loads(session.navigation_stack)
        if not nav_stack:
            return None
        
        # Each item is [screen_id, param]
        previous_item = nav_stack.pop()
        previous_screen, previous_param = previous_item
        
        session.navigation_stack = json.dumps(nav_stack)
        session.current_screen = previous_screen
        session.current_param = previous_param
        session.updated_at = datetime.utcnow()
        
        db.commit()
        return (previous_screen, previous_param)
    finally:
        db.close()


# ==================== FLAGGED QUESTIONS OPERATIONS ====================

def flag_question(question_id: str, reason: str) -> FlaggedQuestion:
    """Flag a question with a reason"""
    db = SessionLocal()
    try:
        flagged = db.query(FlaggedQuestion).filter(
            FlaggedQuestion.question_id == question_id
        ).first()
        
        if not flagged:
            flagged = FlaggedQuestion(
                question_id=question_id,
                flag_count=1,
                reasons=json.dumps([reason])
            )
            db.add(flagged)
        else:
            flagged.flag_count += 1
            reasons = json.loads(flagged.reasons)
            reasons.append(reason)
            flagged.reasons = json.dumps(reasons)
            flagged.last_flagged = datetime.utcnow()
        
        db.commit()
        db.refresh(flagged)
        return flagged
    finally:
        db.close()


def get_flagged_questions(min_flags: int = 1) -> List[FlaggedQuestion]:
    """Get all flagged questions with at least min_flags"""
    db = SessionLocal()
    try:
        return db.query(FlaggedQuestion).filter(
            FlaggedQuestion.flag_count >= min_flags
        ).order_by(FlaggedQuestion.flag_count.desc()).all()
    finally:
        db.close()


# ==================== REVIEW QUEUE OPERATIONS ====================

def add_to_review_queue(
    user_id: int, 
    question_id: str, 
    status: str,
    subject: str,
    grade: int,
    unit: str
) -> ReviewQueue:
    """
    Add a question to the review queue (skipped or mistake).
    Avoids duplicates for the same question/status.
    """
    db = SessionLocal()
    try:
        # Check if already exists
        existing = db.query(ReviewQueue).filter(
            and_(
                ReviewQueue.user_id == user_id,
                ReviewQueue.question_id == question_id
            )
        ).first()
        
        if existing:
            # Update status if changed (e.g. SKIPPED -> MISTAKE)
            # We prioritize MISTAKE over SKIPPED if needed, or just update timestamp
            if existing.status != status:
                existing.status = status
            existing.added_at = datetime.utcnow()
            db.commit()
            db.refresh(existing)
            return existing
        
        # Create new entry
        item = ReviewQueue(
            user_id=user_id,
            question_id=question_id,
            status=status,
            subject=subject,
            grade=grade,
            unit=unit
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        return item
    finally:
        db.close()


def remove_from_review_queue(user_id: int, question_id: str):
    """
    Remove a question from the review queue (because it was answered correctly).
    """
    db = SessionLocal()
    try:
        db.query(ReviewQueue).filter(
            and_(
                ReviewQueue.user_id == user_id,
                ReviewQueue.question_id == question_id
            )
        ).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def get_review_queue_counts(user_id: int, subject: Optional[str] = None, grade: Optional[int] = None) -> dict:
    """
    Get counts of SKIPPED and MISTAKE items.
    Returns: {'SKIPPED': count, 'MISTAKE': count}
    """
    db = SessionLocal()
    try:
        query = db.query(ReviewQueue.status, ReviewQueue.subject).filter(ReviewQueue.user_id == user_id)
        
        if subject:
            # Handle short codes if passed
            subject_map = {"BIO": "Biology", "CHEM": "Chemistry", "PHYS": "Physics", "MATH": "Mathematics"}
            real_subject = subject_map.get(subject, subject)
            if real_subject: # Only filter if we have a valid subject string
                query = query.filter(ReviewQueue.subject == real_subject)
            
        if grade and grade > 0: # Only filter if grade is valid integer > 0
            query = query.filter(ReviewQueue.grade == grade)
            
        results = query.all()
        
        counts = {"SKIPPED": 0, "MISTAKE": 0, "PINNED": 0}
        for status, _ in results:
            if status in counts:
                counts[status] += 1
        return counts
    finally:
        db.close()


def get_review_queue_items(
    user_id: int, 
    status: str, 
    subject: Optional[str] = None, 
    grade: Optional[int] = None
) -> List[ReviewQueue]:
    """
    Get items from review queue for specific criteria.
    """
    db = SessionLocal()
    try:
        query = db.query(ReviewQueue).filter(
            and_(
                ReviewQueue.user_id == user_id,
                ReviewQueue.status == status
            )
        )
        
        if subject:
            subject_map = {"BIO": "Biology", "CHEM": "Chemistry", "PHYS": "Physics", "MATH": "Mathematics"}
            real_subject = subject_map.get(subject, subject)
            query = query.filter(ReviewQueue.subject == real_subject)
            
        if grade:
            query = query.filter(ReviewQueue.grade == grade)
            
        return query.all()
    finally:
        db.close()


# ==================== CHALLENGE OPERATIONS ====================

def create_challenge(creator_id: int, subject: Optional[str], grade: int, questions: list) -> Challenge:
    """Create a new multiplayer challenge"""
    db = SessionLocal()
    try:
        challenge_id = f"CH_{int(time.time())}_{creator_id}"
        challenge = Challenge(
            challenge_id=challenge_id,
            creator_id=creator_id,
            subject=subject,
            grade=grade,
            questions_json=json.dumps(questions)
        )
        db.add(challenge)
        db.commit()
        db.refresh(challenge)
        return challenge
    finally:
        db.close()

def get_challenge(challenge_id: str) -> Optional[dict]:
    """Retrieve a challenge by its ID and return it as a dict to avoid detachment issues."""
    db = SessionLocal()
    try:
        challenge = db.query(Challenge).filter(Challenge.challenge_id == challenge_id).first()
        if challenge:
            return {
                "challenge_id": challenge.challenge_id,
                "subject": challenge.subject,
                "grade": challenge.grade,
                "questions_json": challenge.questions_json
            }
        return None
    finally:
        db.close()
