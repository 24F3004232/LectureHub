import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    SECRET_KEY = os.environ.get('FLASK_SECRET_KEY', 'iitm-scheduler-secret-key-2024')
    
    # ── Fix DATABASE_URL for SQLAlchemy 2.0+ ──
    # Database providers usually give "postgres://" or "postgresql://"
    # SQLAlchemy 2.0 defaults to "psycopg" (v3) for these URLs.
    # We force it to use "psycopg2" since that's what is in requirements.txt.
    database_url = os.environ.get('DATABASE_URL') or os.environ.get('POSTGRES_URL')
    
    if database_url:
        if database_url.startswith('postgres://'):
            database_url = database_url.replace('postgres://', 'postgresql+psycopg2://', 1)
        elif database_url.startswith('postgresql://'):
            database_url = database_url.replace('postgresql://', 'postgresql+psycopg2://', 1)
            
    SQLALCHEMY_DATABASE_URI = database_url
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {'pool_pre_ping': True}

    # Use .get() to prevent KeyError crashes if env vars are missing during build
    SUPABASE_URL = os.environ.get('SUPABASE_URL')
    SUPABASE_ANON_KEY = os.environ.get('SUPABASE_ANON_KEY')
    SUPABASE_JWT_SECRET = os.environ.get('SUPABASE_JWT_SECRET')
    
    # ── Term defaults (for centralized date management) ──
    DEFAULT_TERM_START = '2026-10-04'
    DEFAULT_TERM_END = '2027-01-15'
    DEFAULT_TERM_LABEL = 'September 2026'