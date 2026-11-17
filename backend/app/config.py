"""Load backend environment configuration from backend/.env.

Environment variables supplied by the operating system or deployment platform
always take precedence over values in the local .env file.
"""
from pathlib import Path
from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]
ENV_FILE = BACKEND_DIR / ".env"

# Explicit path means loading works regardless of the process working directory
# (backend/, repository root, IDE launch configuration, or worker process).
load_dotenv(dotenv_path=ENV_FILE, override=False)
