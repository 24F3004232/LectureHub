# D:\iitm_scheduler\app\models.py
from app.extensions import db
from datetime import datetime

class AppUser(db.Model):
    __tablename__ = 'app_user'
    
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.Text, nullable=False, unique=True)
    password_hash = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    subjects = db.relationship('Subject', backref='user', lazy=True, cascade='all, delete-orphan')

class Subject(db.Model):
    __tablename__ = 'subject'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.Text, nullable=False)
    calendar_url = db.Column(db.Text, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('app_user.id', ondelete='CASCADE'), nullable=False, index=True)
    last_synced = db.Column(db.DateTime, nullable=True)
    filter_start = db.Column(db.Date, nullable=True)
    filter_end = db.Column(db.Date, nullable=True)
    ical_etag = db.Column(db.String(100), nullable=True)
    ical_last_modified = db.Column(db.String(100), nullable=True)
    
    events = db.relationship('Event', backref='subject', lazy=True, cascade='all, delete-orphan')

class Event(db.Model):
    __tablename__ = 'event'
    
    id = db.Column(db.Integer, primary_key=True)
    uid = db.Column(db.Text, nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey('subject.id', ondelete='CASCADE'), nullable=False, index=True)
    date = db.Column(db.DateTime, nullable=True)
    end_datetime = db.Column(db.DateTime, nullable=True)
    calendar_title = db.Column(db.Text, nullable=False)
    drive_link = db.Column(db.Text, nullable=True)
    meet_link = db.Column(db.Text, nullable=True)
    youtube_link = db.Column(db.Text, nullable=True)
    user_description = db.Column(db.Text, nullable=True)
    watched = db.Column(db.Boolean, default=False)
    raw_description = db.Column(db.Text, nullable=True)

class DropdownSubject(db.Model):
    """Predefined subjects for dropdown selection"""
    __tablename__ = 'dropdown_subject'
    
    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    name = db.Column(db.Text, nullable=False, unique=True)
    calendar_url = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=datetime.utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
    
    __table_args__ = (
        db.Index('idx_dropdown_subject_name', 'name'),
    )

# Add this class after your existing models

class RecordingArchive(db.Model):
    """Permanent per-subject, per-term snapshot of recording links.
    Extracted once from subject/event tables; deliberately has NO foreign
    keys so it survives subject deletion, sync rewrites and term rotation."""
    __tablename__ = 'recording_archive'

    id = db.Column(db.Integer, primary_key=True)
    subject_name = db.Column(db.Text, nullable=False, index=True)
    term = db.Column(db.String(40), nullable=False, index=True)      # 'May 2026'
    drive_link = db.Column(db.Text, nullable=False)
    meet_link = db.Column(db.Text, nullable=True)
    youtube_link = db.Column(db.Text, nullable=True)
    event_date = db.Column(db.DateTime, nullable=True)               # keeps lecture order
    title = db.Column(db.Text, nullable=True)                        # which lecture it was
    source_event_id = db.Column(db.Integer, nullable=True)           # traceability only, no FK

    # Global week label: visible/editable by all students
    week_number = db.Column(db.Integer, nullable=True, index=True)   # 1 to 12

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        # re-running the extract never duplicates rows
        db.UniqueConstraint('subject_name', 'term', 'drive_link',
                            name='uq_archive_subject_term_link'),
        db.Index('ix_archive_subject_term_date', 'subject_name', 'term', 'event_date'),
    )