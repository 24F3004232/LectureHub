# utils.py
import re
import base64
import datetime as dt_module
import requests
import pytz
from app.models import Subject, Event, RecordingArchive
from app.extensions import db

IST = pytz.timezone('Asia/Kolkata')


def embed_url_to_ical(url):
    """Convert any Google Calendar URL variant to an iCal feed URL."""
    url = url.strip()

    # Already an iCal URL
    if url.endswith('.ics'):
        return url

    # Format 1: dashboard share link — ?cid=base64EncodedCalendarId
    # e.g. calendar.google.com/calendar/u/0/r?cid=Y18z...
    cid_match = re.search(r'[?&]cid=([^&]+)', url)
    if cid_match:
        try:
            cid_b64 = requests.utils.unquote(cid_match.group(1))
            cid_b64 += '=' * (-len(cid_b64) % 4)   # fix base64 padding
            calendar_id = base64.b64decode(cid_b64).decode('utf-8')
            return f"https://calendar.google.com/calendar/ical/{requests.utils.quote(calendar_id)}/public/basic.ics"
        except Exception:
            pass  # fall through to other formats

    # Format 2: embed URL — ?src=calendar_id@group...
    # e.g. calendar.google.com/calendar/embed?src=c_xxx%40group.calendar.google.com
    src_match = re.search(r'[?&]src=([^&]+)', url)
    if src_match:
        calendar_id = requests.utils.unquote(src_match.group(1))
        return f"https://calendar.google.com/calendar/ical/{requests.utils.quote(calendar_id)}/public/basic.ics"

    # Fallback: return as-is and let the HTTP request surface the error
    return url


def extract_calendar_id(url):
    """Extract the raw calendar ID from any Google Calendar URL.
    Works with embed URLs (?src=...) and dashboard share links (?cid=...)."""
    url = url.strip()

    # embed URL: ?src=c_xxx%40group.calendar.google.com
    src_match = re.search(r'[?&]src=([^&]+)', url)
    if src_match:
        return requests.utils.unquote(src_match.group(1))

    # dashboard share link: ?cid=base64EncodedCalendarId
    cid_match = re.search(r'[?&]cid=([^&]+)', url)
    if cid_match:
        try:
            cid_b64 = requests.utils.unquote(cid_match.group(1))
            cid_b64 += '=' * (-len(cid_b64) % 4)
            return base64.b64decode(cid_b64).decode('utf-8')
        except Exception:
            pass

    return None


def extract_links(text):
    """Extract Google Meet and Drive/recording links from event description text."""
    if not text:
        return None, None

    text = str(text)
    meet_link = None
    drive_link = None

    # Meet link
    meet_match = re.search(r'(?:href=")?(https?://meet\.google\.com/[^\s>"\'<\]]+)', text)
    if meet_match:
        meet_link = meet_match.group(1)

    # Drive / recording links — ordered by specificity
    drive_patterns = [
        r'(?:href=")?(https?://drive\.google\.com/[^\s>"\'<\]]+)',
        r'(?:href=")?(https?://docs\.google\.com/[^\s>"\'<\]]+)',
        r'(?:href=")?(https?://storage\.cloud\.google\.com/[^\s>"\'<\]]+)',
    ]
    for pattern in drive_patterns:
        match = re.search(pattern, text)
        if match:
            drive_link = match.group(1)
            break

    return meet_link, drive_link


def extract_attachments(component):
    """Return a list of attachment dicts from a VEVENT component."""
    attachments = []
    attach = component.get('ATTACH')
    if not attach:
        return attachments
    if not isinstance(attach, list):
        attach = [attach]
    for a in attach:
        params = getattr(a, 'params', {}) or {}
        attachments.append({
            'url':      str(a),
            'fmt_type': str(params.get('FMTTYPE', '')),
            'title':    str(params.get('X-GOOGLE-CALENDAR-CONTENT-TITLE', '')),
        })
    return attachments


def pick_video_link(attachments):
    """Pick the best recording URL from a list of attachment dicts."""
    if not attachments:
        return None
    # Prefer explicitly typed video attachments
    for att in attachments:
        if 'video' in att['fmt_type'].lower():
            return att['url']
    # Then look for recording/session keywords in title
    for att in attachments:
        title = att['title'].lower()
        if 'recording' in title or 'session' in title:
            return att['url']
    # Fallback: first attachment
    return attachments[0]['url']


def to_datetime(dt_val):
    """Convert an icalendar date/datetime value to an IST-aware datetime."""
    if dt_val is None:
        return None
    val = dt_val.dt if hasattr(dt_val, 'dt') else dt_val
    if isinstance(val, dt_module.datetime):
        if val.tzinfo is None:
            val = IST.localize(val)
        else:
            val = val.astimezone(IST)
        return val
    if isinstance(val, dt_module.date):
        val = dt_module.datetime(val.year, val.month, val.day)
        return IST.localize(val)
    return None


def extract_drive_file_id(url):
    """Extract a Google Drive file ID from any Drive URL format."""
    if not url:
        return None
    match = re.search(r'/file/d/([a-zA-Z0-9_-]+)', url)
    if match:
        return match.group(1)
    match = re.search(r'[?&]id=([a-zA-Z0-9_-]+)', url)
    if match:
        return match.group(1)
    return None


def drive_embed_url(url):
    """Convert a Google Drive share URL to an embeddable preview URL."""
    file_id = extract_drive_file_id(url)
    if file_id:
        return f"https://drive.google.com/file/d/{file_id}/preview"
    return None

def extract_recordings_to_archive(term_label='May 2026'):
    """Copy every Drive link from event tables into recording_archive.
    Idempotent: safe to run any number of times. Returns (added, skipped)."""
    existing = {
        (r.subject_name, r.drive_link)
        for r in RecordingArchive.query.filter_by(term=term_label).all()
    }

    rows = (
        db.session.query(Subject, Event)
        .join(Event, Event.subject_id == Subject.id)
        .filter(Event.drive_link.isnot(None), Event.drive_link != '')
        .order_by(Subject.name.asc(), Event.date.asc())
        .all()
    )

    added = 0
    for subj, ev in rows:
        link = (ev.drive_link or '').strip()
        if not link or (subj.name, link) in existing:
            continue
        db.session.add(RecordingArchive(
            subject_name=subj.name,
            term=term_label,
            drive_link=link,
            meet_link=(ev.meet_link or '').strip() or None,
            youtube_link=(ev.youtube_link or '').strip() or None,
            event_date=ev.date,
            title=(ev.calendar_title or '').strip() or None,
            source_event_id=ev.id,
        ))
        existing.add((subj.name, link))
        added += 1

    db.session.commit()
    return added, len(rows) - added