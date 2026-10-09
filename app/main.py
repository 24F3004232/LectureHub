# D:\LectureHub-github\app\main.py
from flask import Blueprint, render_template, redirect, url_for, request, flash, jsonify, send_from_directory, current_app
from datetime import datetime
import pytz
from app.extensions import db
from app.models import Subject, Event, RecordingArchive
from app.auth import login_required, current_user
from app.utils import drive_embed_url
from app.sync import sync_subject, sync_subject_with_token

main_bp = Blueprint('main', __name__)
IST = pytz.timezone('Asia/Kolkata')

def youtube_embed_url(raw_url: str) -> str:
    """Convert any YouTube watch/short/live URL to an embed URL."""
    if not raw_url:
        return ''
    from urllib.parse import urlparse, parse_qs
    try:
        p = urlparse(raw_url)
        host = p.hostname or ''
        vid = ''
        if 'youtu.be' in host:
            vid = p.path.lstrip('/')
        elif 'youtube.com' in host:
            qs = parse_qs(p.query)
            if 'v' in qs:
                vid = qs['v'][0]
            else:
                # handles /watch/, /shorts/, /live/, /embed/
                parts = [s for s in p.path.split('/') if s]
                if len(parts) >= 2 and parts[0] in ('shorts', 'live', 'embed'):
                    vid = parts[1]
        vid = vid.split('&')[0].split('?')[0].strip()
        return f'https://www.youtube.com/embed/{vid}' if vid else ''
    except Exception:
        return ''


@main_bp.route('/')
def landing():
    """Public landing page. Logged-in users go straight to dashboard."""
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))
    return render_template('landing.html')


@main_bp.route('/login')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))
    return redirect(url_for('auth.login'))

@main_bp.route('/setup', methods=['GET', 'POST'])
@login_required
def setup():
    if request.method == 'POST':
        names = request.form.getlist('subject_name')
        urls = request.form.getlist('calendar_url')
        starts = request.form.getlist('filter_start')
        ends = request.form.getlist('filter_end')

        added = 0
        for name, url, start_str, end_str in zip(names, urls, starts, ends):
            name = name.strip()
            url = url.strip()
            if not name or not url:
                continue

            filter_start = None
            filter_end = None
            if start_str:
                try:
                    filter_start = datetime.strptime(start_str, '%Y-%m-%d').date()
                except ValueError:
                    flash(f'Invalid start date for "{name}".', 'error')
                    continue
            if end_str:
                try:
                    filter_end = datetime.strptime(end_str, '%Y-%m-%d').date()
                except ValueError:
                    flash(f'Invalid end date for "{name}".', 'error')
                    continue

            if filter_start and filter_end and filter_start > filter_end:
                flash(f'Start date must be before end date for "{name}".', 'error')
                continue

            subj = Subject(
                name=name,
                calendar_url=url,
                user_id=current_user.id,
                filter_start=filter_start,
                filter_end=filter_end
            )
            db.session.add(subj)
            db.session.flush()
            sync_subject(subj)
            added += 1

        db.session.commit()
        if added:
            flash(f'{added} subject(s) added and synced!', 'success')
            return redirect(url_for('main.dashboard'))
        else:
            flash('Please add at least one subject with a calendar link.', 'error')
    return render_template('setup.html')

@main_bp.route('/dashboard')
@login_required
def dashboard():
    subjects = Subject.query.filter_by(user_id=current_user.id).all()
    if not subjects:
        return redirect(url_for('main.setup'))

    active_tab = request.args.get('tab', str(subjects[0].id))
    events_by_subject = {}
    for subj in subjects:
        evs = Event.query.filter_by(subject_id=subj.id).order_by(Event.date.asc()).all()
        for ev in evs:
            if ev.end_datetime is not None and ev.end_datetime.tzinfo is None:
                ev.end_datetime = IST.localize(ev.end_datetime)
        events_by_subject[subj.id] = evs

    now = datetime.now(IST)
    return render_template('dashboard.html',
                           subjects=subjects,
                           events_by_subject=events_by_subject,
                           active_tab=active_tab,
                           now=now)

@main_bp.route('/event/<int:event_id>/youtube', methods=['POST'])
@login_required
def update_youtube(event_id):
    ev = Event.query.join(Subject).filter(
        Event.id == event_id, Subject.user_id == current_user.id
    ).first_or_404()
    data = request.get_json(silent=True) or {}
    print("DEBUG youtube route hit, event_id:", event_id)
    print("DEBUG raw data:", data)
    print("DEBUG youtube_link value:", data.get('youtube_link'))
    ev.youtube_link = data.get('youtube_link', '').strip() or None
    db.session.commit()
    print("DEBUG after commit, ev.youtube_link:", ev.youtube_link)
    return jsonify({'success': True, 'youtube_link': ev.youtube_link})

@main_bp.route('/event/<int:event_id>')
@login_required
def event_detail(event_id):
    ev = Event.query.join(Subject).filter(
        Event.id == event_id, Subject.user_id == current_user.id
    ).first_or_404()
    subj = Subject.query.get(ev.subject_id)

    if ev.date is not None and ev.date.tzinfo is None:
        ev.date = IST.localize(ev.date)
    if ev.end_datetime is not None and ev.end_datetime.tzinfo is None:
        ev.end_datetime = IST.localize(ev.end_datetime)

    # Drive embed takes priority; fall back to YouTube embed
    embed_url = drive_embed_url(ev.drive_link) or youtube_embed_url(ev.youtube_link or '')
    now = datetime.now(IST)

    return render_template('event.html',
                           ev=ev,
                           subject=subj,
                           embed_url=embed_url,
                           now=now)


@main_bp.route('/sync/<int:subject_id>', methods=['POST'])
@login_required
def sync(subject_id):
    subj = Subject.query.filter_by(id=subject_id, user_id=current_user.id).first_or_404()
    
    # Token-relay path: JS sent a short-lived Google token from the browser session
    if request.is_json and request.json.get('google_token'):
        ok, msg = sync_subject_with_token(subj, request.json['google_token'])
    else:
        ok, msg = sync_subject(subj)
    
    return jsonify({'ok': ok, 'msg': msg})

@main_bp.route('/sync_all', methods=['POST'])
@login_required
def sync_all():
    subjects = Subject.query.filter_by(user_id=current_user.id).all()

    # Token-relay path: JS sent a short-lived Google token from the browser session
    google_token = None
    if request.is_json:
        google_token = request.json.get('google_token')

    results = []
    for subj in subjects:
        if google_token:
            ok, msg = sync_subject_with_token(subj, google_token)
        else:
            ok, msg = sync_subject(subj)
        results.append({'subject': subj.name, 'ok': ok, 'msg': msg})

    return jsonify({'results': results})

@main_bp.route('/event/<int:event_id>/description', methods=['POST'])
@login_required
def update_description(event_id):
    ev = Event.query.join(Subject).filter(
        Event.id == event_id, Subject.user_id == current_user.id
    ).first_or_404()
    ev.user_description = request.json.get('description', '')
    db.session.commit()
    return jsonify({'ok': True})

@main_bp.route('/event/<int:event_id>/watched', methods=['POST'])
@login_required
def toggle_watched(event_id):
    ev = Event.query.join(Subject).filter(
        Event.id == event_id, Subject.user_id == current_user.id
    ).first_or_404()
    ev.watched = not ev.watched
    db.session.commit()
    return jsonify({'ok': True, 'watched': ev.watched})

@main_bp.route('/add_subject', methods=['GET', 'POST'])
@login_required
def add_subject():
    if request.method == 'POST':
        name = request.form.get('subject_name', '').strip()
        url = request.form.get('calendar_url', '').strip()
        start_str = request.form.get('filter_start', '')
        end_str = request.form.get('filter_end', '')

        if not name or not url:
            flash('Both subject name and calendar URL are required.', 'error')
            return render_template('add_subject.html')

        filter_start = None
        filter_end = None
        if start_str:
            try:
                filter_start = datetime.strptime(start_str, '%Y-%m-%d').date()
            except ValueError:
                flash('Invalid start date.', 'error')
                return render_template('add_subject.html')
        if end_str:
            try:
                filter_end = datetime.strptime(end_str, '%Y-%m-%d').date()
            except ValueError:
                flash('Invalid end date.', 'error')
                return render_template('add_subject.html')

        if filter_start and filter_end and filter_start > filter_end:
            flash('Start date must be before end date.', 'error')
            return render_template('add_subject.html')

        subj = Subject(
            name=name,
            calendar_url=url,
            user_id=current_user.id,
            filter_start=filter_start,
            filter_end=filter_end
        )
        db.session.add(subj)
        db.session.flush()
        sync_subject(subj)
        db.session.commit()
        flash(f'Subject "{name}" added!', 'success')
        return redirect(url_for('main.dashboard', tab=str(subj.id)))
    return render_template('add_subject.html')

@main_bp.route('/delete_subject/<int:subject_id>', methods=['POST'])
@login_required
def delete_subject(subject_id):
    subj = Subject.query.filter_by(id=subject_id, user_id=current_user.id).first_or_404()
    name = subj.name
    db.session.delete(subj)
    db.session.commit()
    flash(f'Subject "{name}" removed.', 'success')
    return redirect(url_for('main.dashboard'))


# ── Resources: past-term recording archive ────────────────────────────────────

MONTH_ORDER = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

def term_sort_key(term):
    """Sort terms like 'May 2026' chronologically."""
    if not term:
        return (0, 0)
    try:
        dt = datetime.strptime(term.strip(), "%B %Y")
        return (dt.year, dt.month)
    except Exception:
        parts = term.strip().split()
        if len(parts) == 2:
            month_name = parts[0][:3].lower()
            month = MONTH_ORDER.get(month_name, 0)
            try:
                year = int(parts[1])
            except Exception:
                year = 0
            return (year, month)
        return (0, 0)

def archive_sort_key(rec):
    """Sort recordings by assigned week first, then by date."""
    return (
        rec.week_number is None,
        rec.week_number if rec.week_number is not None else 999,
        rec.event_date is None,
        rec.event_date or datetime.min,
    )

@main_bp.route('/resources')
@login_required
def resources():
    """
    Resources page. Requires explicit Term and Subject selection to show data.
    """
    term_arg = request.args.get("term")
    subject_arg = request.args.get("subject")

    # Get all distinct terms and sort them chronologically (newest first)
    term_rows = db.session.query(RecordingArchive.term).distinct().all()
    terms = sorted([t[0] for t in term_rows if t[0]], key=term_sort_key, reverse=True)

    # DO NOT default to the latest term. Require user to select.
    selected_term = term_arg.strip() if term_arg else None
    selected_subject = subject_arg.strip() if subject_arg else None

    subjects = []
    recordings = []

    # Only fetch subjects if a term is selected
    if selected_term:
        subject_query = (
            db.session.query(RecordingArchive.subject_name)
            .distinct()
            .filter(RecordingArchive.term == selected_term)
        )
        subjects = sorted([s[0] for s in subject_query.all() if s[0]])
        
        # Only fetch recordings if BOTH term and subject are selected
        if selected_subject:
            query = RecordingArchive.query.filter(
                RecordingArchive.term == selected_term,
                RecordingArchive.subject_name == selected_subject
            )
            recordings = query.all()
            recordings.sort(key=archive_sort_key)

    return render_template(
        'resources.html',
        recordings=recordings,
        terms=terms,
        subjects=subjects,
        selected_term=selected_term,
        selected_subject=selected_subject,
        week_options=range(1, 13),
    )


@main_bp.route('/resources/<int:archive_id>/week', methods=['POST'])
@login_required
def update_resource_week(archive_id):
    """Global week update endpoint. Any student can update it."""
    rec = RecordingArchive.query.get_or_404(archive_id)
    data = request.get_json(silent=True) or {}
    week = data.get('week', request.form.get('week', ''))

    if week in [None, "", "none"]:
        rec.week_number = None
    else:
        try:
            week_value = int(week)
        except Exception:
            return jsonify(ok=False, message="Invalid week value"), 400

        if week_value < 1 or week_value > 12:
            return jsonify(ok=False, message="Week must be between 1 and 12"), 400

        rec.week_number = week_value

    db.session.commit()
    return jsonify(ok=True, week_number=rec.week_number)


@main_bp.route('/resources/<int:archive_id>')
@login_required
def resource_detail(archive_id):
    """Internal detail page for a resource, styled like event_detail."""
    rec = RecordingArchive.query.get_or_404(archive_id)
    
    # Drive embed takes priority; fall back to YouTube embed
    embed_url = drive_embed_url(rec.drive_link) or youtube_embed_url(rec.youtube_link or '')
    
    return render_template(
        'resource_detail.html',
        rec=rec,
        embed_url=embed_url,
        week_options=range(1, 13),
    )


@main_bp.route('/robots.txt')
def robots():
    """Allow crawlers, block private routes, point to sitemap."""
    body = (
        "# LECFLOW robots.txt\n"
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /dashboard\n"
        "Disallow: /setup\n"
        "Disallow: /add_subject\n"
        "Disallow: /event/\n"
        "Disallow: /subject/\n"
        "Disallow: /sync\n"
        "Disallow: /sync_all\n"
        "Disallow: /delete_subject\n"
        "Disallow: /resources\n"   # Added resources to disallow for crawlers
        "\n"
        "# AI assistants welcome on public pages.\n"
        "Sitemap: https://lecflow.space/sitemap.xml\n"
    )
    return body, 200, {'Content-Type': 'text/plain; charset=utf-8'}


@main_bp.route('/sitemap.xml')
def sitemap():
    """Simple sitemap for public pages."""
    from datetime import datetime
    today = datetime.now().date().isoformat()
    urls = [
        ('https://lecflow.space/',          '1.0', 'weekly'),
        ('https://lecflow.space/register',  '0.8', 'monthly'),
        ('https://lecflow.space/login',     '0.5', 'monthly'),
    ]
    items = ''.join(
        f'<url><loc>{loc}</loc><lastmod>{today}</lastmod>'
        f'<changefreq>{freq}</changefreq><priority>{prio}</priority></url>'
        for loc, prio, freq in urls
    )
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
           + items + '</urlset>')
    return xml, 200, {'Content-Type': 'application/xml; charset=utf-8'}

@main_bp.route('/llms.txt')
def llms_txt():
    """llms.txt — emerging standard for AI assistants to understand the site."""
    body = """# LECFLOW

> LECFLOW is a free TA and Instructor lecture organiser for IIT Madras BS Online Degree
> students. It syncs public course calendars, surfaces live Google Meet links and
> Drive/YouTube recordings per lecture, and provides a Markdown notes editor with
> KaTeX math, autosave and PDF export, plus watched-progress tracking and a
> term-wise recording archive.

## Key facts
- Free: no credit card required
- Audience: IITM BS Online Degree students (Data Science, Electronics, etc.)
- Public calendars sync automatically; private calendars supported via a manual schedule planner
- Notes are private per user; recordings stay on the institute's Drive/YouTube
- Not affiliated with IIT Madras; independent student-built tool

## Links
- [Get started](https://lecflow.space/register): Create a free account
- [Sign in](https://lecflow.space/login)
- [Features](https://lecflow.space/#features)
- [How it works](https://lecflow.space/#how)
- [FAQ](https://lecflow.space/#faq)
"""
    return body, 200, {'Content-Type': 'text/plain; charset=utf-8'}


@main_bp.route('/privacy')
def privacy():
    return render_template('privacy.html')

@main_bp.route('/terms')
def terms():
    return render_template('terms.html')