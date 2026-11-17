"""Apply the checked-in initial schema migration or create a local SQLite dev schema."""
from . import config as _config  # Load backend/.env before selecting the database.
import os
from pathlib import Path
from .db import init_db
if __name__=='__main__':
    url=os.getenv('DATABASE_URL','sqlite:///./supplykey.db')
    if url.startswith('postgresql'):
        import psycopg
        sql=(Path(__file__).resolve().parent.parent/'migrations'/'001_initial.sql').read_text()
        url=url.replace('postgresql+psycopg://','postgresql://',1)
        with psycopg.connect(url) as conn:
            conn.execute(sql,prepare=False)
        print('PostgreSQL migration 001 applied.')
    else:
        init_db();print('SQLite development schema created/verified.')
