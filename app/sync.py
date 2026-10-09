# D:\iitm_scheduler\app\sync.py
import re
import requests
import datetime as dt_module
from datetime import datetime
from icalendar import Calendar
from recurring_ical_events import of
from app.extensions import db
from app.models import Subject, Event
from app.utils import embed_url_to_ical, extract_links, to_datetime
import pytz

def sync_subject(subject):
    ical_url = embed_url_to_ical(subject.calendar_url)
    try:
        resp = requests.get(ical_url, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        return False, str(e)

    try:
        cal = Calendar.from_ical(resp.content)
    except Exception as e:
        return False, f"Could not parse calendar: {e}"

    if subject.filter_start:
        start_date = dt_module.datetime.combine(subject.filter_start, dt_module.time.min)
    else:
        start_date = dt_module.datetime(2020, 1, 1)
    if subject.filter_end:
        end_date = dt_module.datetime.combine(subject.filter_end, dt_module.time.max)
    else:
        end_date = dt_module.datetime(2030, 12, 31)

    try:
        occurrences = of(cal).between(start_date, end_date)
    except Exception as e:
        return False, f"Recurrence expansion failed: {e}"

    existing_events = Event.query.filter_by(subject_id=subject.id).all()
    existing = {ev.uid: ev for ev in existing_events}

    count = 0
    for occurrence in occurrences:
        component = occurrence
        if component.name != 'VEVENT':
            continue

        ical_uid = str(component.get('UID', ''))
        start = to_datetime(component.get('DTSTART'))
        end = to_datetime(component.get('DTEND'))

        if start is None:
            continue

        occurrence_uid = f"{ical_uid}_{start.isoformat()}"

        title = str(component.get('SUMMARY', 'Untitled'))
        raw_desc = str(component.get('DESCRIPTION', '') or '')

        meet_link, drive_link = extract_links(raw_desc)
        attachments = component.get('ATTACH')
        if attachments and not drive_link:
            if not isinstance(attachments, list):
                attachments = [attachments]
            for att in attachments:
                url = str(att) if isinstance(att, str) else str(att.get('value', ''))
                if re.search(r'(drive\.google\.com|docs\.google\.com|storage\.cloud\.google\.com)', url):
                    drive_link = url
                    break

        if occurrence_uid in existing:
            ev = existing[occurrence_uid]
            ev.calendar_title = title
            ev.date = start
            ev.end_datetime = end
            ev.raw_description = raw_desc
            if drive_link:
                ev.drive_link = drive_link
            if meet_link:
                ev.meet_link = meet_link
        else:
            ev = Event(
                uid=occurrence_uid,
                subject_id=subject.id,
                calendar_title=title,
                date=start,
                end_datetime=end,
                raw_description=raw_desc,
                meet_link=meet_link,
                drive_link=drive_link,
            )
            db.session.add(ev)
        count += 1

    db.session.commit()
    subject.last_synced = datetime.utcnow()
    db.session.commit()
    return True, f"Synced {count} occurrences"


def _parse_api_datetime(val_str):
    """Convert a Google Calendar API date/dateTime string to IST-aware datetime.
    Mirrors what to_datetime() does for ICS values."""
    IST = pytz.timezone('Asia/Kolkata')
    if not val_str:
        return None
    try:
        if 'T' in val_str:
            dt = datetime.fromisoformat(val_str.replace('Z', '+00:00'))
            return dt.astimezone(IST)
        else:
            d = dt_module.datetime.strptime(val_str, '%Y-%m-%d')
            return IST.localize(d)
    except Exception:
        return None


def sync_subject_with_token(subject, google_token):
    """Fetch restricted calendar events using a short-lived Google token
    obtained from the student's existing browser session via GIS.

    This is the server-side half of the client-side token relay:
      browser (student already logged into IITM Google)
        → GIS silently gets a read-only token
          → JS POSTs token here
            → Flask calls Calendar API with it
              → same DB logic as sync_subject()

    Your Flask auth is completely untouched.
    """
    from app.utils import extract_calendar_id
    from urllib.parse import quote

    calendar_id = extract_calendar_id(subject.calendar_url)
    if not calendar_id:
        return False, "Could not extract calendar ID from the stored URL"

    if subject.filter_start:
        time_min = dt_module.datetime.combine(
            subject.filter_start, dt_module.time.min
        ).isoformat() + "Z"
    else:
        time_min = "2020-01-01T00:00:00Z"

    if subject.filter_end:
        time_max = dt_module.datetime.combine(
            subject.filter_end, dt_module.time.max
        ).isoformat() + "Z"
    else:
        time_max = "2030-12-31T23:59:59Z"

    api_url = (
        f"https://www.googleapis.com/calendar/v3"
        f"/calendars/{quote(calendar_id, safe='')}/events"
        f"?singleEvents=true&orderBy=startTime"
        f"&timeMin={time_min}&timeMax={time_max}"
        f"&maxResults=250"
    )

    try:
        resp = requests.get(
            api_url,
            headers={"Authorization": f"Bearer {google_token}"},
            timeout=15
        )
        if resp.status_code == 403:
            return False, "Access denied — make sure you're logged into your IITM Google account in this browser"
        if resp.status_code == 404:
            return False, "Calendar not found — check the calendar URL is correct"
        resp.raise_for_status()
    except Exception as e:
        return False, f"Google Calendar API error: {e}"

    items = resp.json().get("items", [])

    # ── DB logic: identical to sync_subject() from here ──────────────────────
    existing_events = Event.query.filter_by(subject_id=subject.id).all()
    existing = {ev.uid: ev for ev in existing_events}

    count = 0
    for item in items:
        ical_uid  = item.get("iCalUID", item.get("id", ""))
        start_str = item["start"].get("dateTime", item["start"].get("date"))
        end_str   = item["end"].get("dateTime",   item["end"].get("date"))

        start = _parse_api_datetime(start_str)
        end   = _parse_api_datetime(end_str)

        if start is None:
            continue

        occurrence_uid = f"{ical_uid}_{start.isoformat()}"

        title       = item.get("summary", "Untitled")
        description = item.get("description", "") or ""
        meet_link, drive_link = extract_links(description)

        # Attachments (Google API returns these separately from description)
        for att in item.get("attachments", []):
            file_url = att.get("fileUrl", "")
            if re.search(r'drive\.google\.com', file_url) and not drive_link:
                drive_link = file_url

        if occurrence_uid in existing:
            ev = existing[occurrence_uid]
            ev.calendar_title  = title
            ev.date            = start
            ev.end_datetime    = end
            ev.raw_description = description
            ev.drive_link      = drive_link or ev.drive_link  # keep old if none returned
            ev.meet_link       = meet_link  or ev.meet_link
        else:
            ev = Event(
                uid=occurrence_uid,
                subject_id=subject.id,
                calendar_title=title,
                date=start,
                end_datetime=end,
                raw_description=description,
                meet_link=meet_link,
                drive_link=drive_link,
            )
            db.session.add(ev)
        count += 1

    db.session.commit()
    subject.last_synced = datetime.utcnow()
    db.session.commit()
    return True, f"Synced {count} events"