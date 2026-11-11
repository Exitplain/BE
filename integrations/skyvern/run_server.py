"""Launch upstream Skyvern on loopback; implementation remains in the installed package."""
from pathlib import Path
from dotenv import load_dotenv
import uvicorn

if __name__=='__main__':
    load_dotenv(Path.home()/'.cache/dropspot-skyvern-work/.env')
    uvicorn.run('skyvern.forge.api_app:create_api_app',factory=True,host='127.0.0.1',port=8000,
        loop='asyncio',ws='websockets-sansio',access_log=False,log_level='warning')
