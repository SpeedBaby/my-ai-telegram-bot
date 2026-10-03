"""
Compatibility entry point: local run without a webhook (long polling).

    python polling.py

The actual loop lives in bot.py:run_polling(), so `python bot.py` does the same.
For 24/7 without your PC deploy bot.py to Render (see README.md / SETUP_RU.md).
"""

import asyncio

from bot import log, run_polling

if __name__ == "__main__":
    try:
        asyncio.run(run_polling())
    except KeyboardInterrupt:
        log.info("Stopped by user")
