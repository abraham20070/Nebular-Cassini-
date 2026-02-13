"""
Database models for Nebular Cassini Bot
"""
from sqlalchemy import Boolean, Column, Integer, BigInteger, String, Float, DateTime, Text, ForeignKey
from sqlalchemy.types import TypeDecorator
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime

class SafeDateTime(TypeDecorator):
    """
    Robust DateTime type that handles various string formats from SQLite.
    Specific fix for 'ValueError: Couldn't parse datetime string' with ISO format including 'T'.
    """
    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return value

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
            
        try:
            return datetime.strptime(value, "%Y-%m-%d %H:%M:%S.%f")
        except ValueError:
            pass
            
        try:
            return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
            
        return value

Base = declarative_base()


class User(Base):
    """Core user information and gamification data"""
    __tablename__ = 'users'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False, index=True)
    username = Column(String(255), nullable=True)
    full_name = Column(String(255), nullable=False)
    join_date = Column(SafeDateTime, default=datetime.utcnow, nullable=False)
    current_grade = Column(Integer, default=9, nullable=False)  # 9-12
    streak_count = Column(Integer, default=0, nullable=False)
    last_activity = Column(SafeDateTime, default=datetime.utcnow, nullable=False, index=True)
    total_xp = Column(Integer, default=0, nullable=False)
    weekly_xp = Column(Integer, default=0, nullable=False)  # XP earned this week
    week_start_date = Column(SafeDateTime, default=datetime.utcnow, nullable=False)  # When current week started
    level = Column(Integer, default=1, nullable=False)
    best_subject = Column(String(50), nullable=True)
    language = Column(String(10), default="EN", nullable=False)
    notifications_enabled = Column(Boolean, default=True, nullable=False)
    
    # Relationships
    progress_records = relationship("Progress", back_populates="user", cascade="all, delete-orphan")
    sessions = relationship("Session", back_populates="user", cascade="all, delete-orphan")
    review_items = relationship("ReviewQueue", back_populates="user", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<User(telegram_id={self.telegram_id}, name='{self.full_name}', level={self.level})>"


class Progress(Base):
    """Curriculum phase progression per unit"""
    __tablename__ = 'progress'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    subject = Column(String(50), nullable=False)  # Biology, Chemistry, Physics, Mathematics
    grade = Column(Integer, nullable=False)  # 9-12
    unit_id = Column(String(100), nullable=False, index=True)  # e.g., "BIO_G11_U2"
    
    # Phase tracking
    current_phase = Column(String(20), default="BASELINE", nullable=False)  # BASELINE, BALANCED, EXAM_BIASED
    baseline_accuracy = Column(Float, default=0.0, nullable=False)  # 0.0-100.0
    balanced_accuracy = Column(Float, default=0.0, nullable=False)
    exam_accuracy = Column(Float, default=0.0, nullable=False)
    
    # Statistics
    questions_attempted = Column(Integer, default=0, nullable=False)
    questions_correct = Column(Integer, default=0, nullable=False)
    completion_percent = Column(Float, default=0.0, nullable=False)
    
    # Phase unlocks
    unlocked_balanced = Column(Boolean, default=False, nullable=False)
    unlocked_exam = Column(Boolean, default=False, nullable=False)
    
    # Relationship
    user = relationship("User", back_populates="progress_records")
    
    def __repr__(self):
        return f"<Progress(unit={self.unit_id}, phase={self.current_phase}, completion={self.completion_percent}%)>"


class Session(Base):
    """Session state for message editing and bot restart recovery"""
    __tablename__ = 'sessions'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.id'), unique=True, nullable=False, index=True)
    current_screen = Column(String(50), default="SCR_HUB", nullable=False)
    current_param = Column(String(255), nullable=True)
    navigation_stack = Column(Text, default="[]", nullable=False)  # JSON array of (screen_id, param)
    last_message_id = Column(BigInteger, nullable=True)  # The message to edit
    session_active = Column(Boolean, default=True, nullable=False)
    quiz_state = Column(Text, nullable=True)  # JSON: current quiz data
    updated_at = Column(SafeDateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relationship
    user = relationship("User", back_populates="sessions")
    
    def __repr__(self):
        return f"<Session(user_id={self.user_id}, screen={self.current_screen}, msg_id={self.last_message_id})>"


class FlaggedQuestion(Base):
    """Admin analytics for question quality"""
    __tablename__ = 'flagged_questions'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    question_id = Column(String(255), unique=True, nullable=False, index=True)
    flag_count = Column(Integer, default=1, nullable=False)
    reasons = Column(Text, default="[]", nullable=False)  # JSON array of flag reasons
    last_flagged = Column(SafeDateTime, default=datetime.utcnow, nullable=False)
    
    def __repr__(self):
        return f"<FlaggedQuestion(id={self.question_id}, count={self.flag_count})>"


class ReviewQueue(Base):
    """
    Tracks individual questions that need review (either skipped or incorrect).
    Items are removed automatically when answered correctly.
    """
    __tablename__ = 'review_queue'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    
    # Metadata to help filtering
    subject = Column(String(50), nullable=False)
    grade = Column(Integer, nullable=False)
    unit = Column(String(100), nullable=False)
    
    question_id = Column(String(255), nullable=False) # The unique ID of the question from JSON
    
    status = Column(String(20), nullable=False) # 'SKIPPED' or 'MISTAKE'
    added_at = Column(SafeDateTime, default=datetime.utcnow, nullable=False)
    
    # Relationship
    user = relationship("User", back_populates="review_items")
    
    def __repr__(self):
        return f"<ReviewQueue(user={self.user_id}, q={self.question_id}, status={self.status})>"


class Challenge(Base):
    """Multiplayer challenge data"""
    __tablename__ = 'challenges'

    id = Column(Integer, primary_key=True, autoincrement=True)
    challenge_id = Column(String(100), unique=True, nullable=False, index=True)
    creator_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    subject = Column(String(50), nullable=True) # None for mixed
    grade = Column(Integer, nullable=False)
    questions_json = Column(Text, nullable=False) # JSON list of questions
    creator_score = Column(Integer, default=0, nullable=False) # Score of the creator (out of 10)
    created_at = Column(SafeDateTime, default=datetime.utcnow, nullable=False)

    creator = relationship("User")


class SystemLock(Base):
    """Admin-controlled locks for features, grades, subjects, and units"""
    __tablename__ = 'system_locks'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    lock_type = Column(String(20), nullable=False, index=True)  # FEATURE, GRADE, SUBJECT, UNIT
    lock_target = Column(String(255), nullable=False, index=True)  # e.g., "GAME_MODE", "Grade 12", "Biology", "BIO_G12_U1"
    is_locked = Column(Boolean, default=False, nullable=False)
    locked_by = Column(BigInteger, nullable=True)  # Admin telegram_id who set the lock
    locked_at = Column(SafeDateTime, default=datetime.utcnow, nullable=False)
    lock_reason = Column(Text, nullable=True)  # Optional reason for the lock
    
    def __repr__(self):
        status = "LOCKED" if self.is_locked else "UNLOCKED"
        return f"<SystemLock({self.lock_type}:{self.lock_target} = {status})>"
