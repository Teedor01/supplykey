"""Apply all checked-in PostgreSQL migrations or create/upgrade a local SQLite dev schema."""
from . import config as _config  # Load backend/.env before selecting the database.
import os
from pathlib import Path
from .db import init_db
if __name__=='__main__':
    url=os.getenv('DATABASE_URL','sqlite:///./supplykey.db')
    if url.startswith('postgresql'):
        import psycopg
        migration_dir=Path(__file__).resolve().parent.parent/'migrations'
        url=url.replace('postgresql+psycopg://','postgresql://',1)
        with psycopg.connect(url) as conn:
            for name in ['001_initial.sql','002_settlement_classification.sql','003_verified_beneficiary_binding.sql']:
                conn.execute((migration_dir/name).read_text(),prepare=False)
                print(f'PostgreSQL migration {name} applied/verified.')
    else:
        init_db();print('SQLite development schema created/verified.')
