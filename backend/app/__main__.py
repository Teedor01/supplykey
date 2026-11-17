from . import config as _config  # Load backend/.env before reading PORT.
import uvicorn
uvicorn.run('app.main:app',host='0.0.0.0',port=int(__import__('os').getenv('PORT','8000')),reload=False)
