"""Shared test environment.

Every test module shares one isolated SQLite database. The environment is set
before ``app`` is imported anywhere, using the same values as test_api.py so the
modules agree regardless of collection order. Nothing here touches a real
database, real Paga credentials or real money.
"""
import os
import tempfile
from pathlib import Path

DB = Path(tempfile.gettempdir()) / f'supplykey-tests-{os.getpid()}.db'
os.environ['DATABASE_URL'] = f'sqlite:///{DB}'
os.environ['APP_ENV'] = 'development'
os.environ['SUPPLYKEY_SECRET'] = 'test-secret'
os.environ.setdefault('PAYMENT_PROVIDER', 'simulated')
