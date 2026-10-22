
import signal
import subprocess
import sys
import time
import urllib.request
import urllib.error

web = None
bot = None
stopping = False


def stop_all(*_):
    global stopping
    stopping = True
    for proc in (bot, web):
        if proc is not None and proc.poll() is None:
            proc.terminate()


signal.signal(signal.SIGTERM, stop_all)
signal.signal(signal.SIGINT, stop_all)

web = subprocess.Popen([sys.executable, "web_server.py"])
deadline = time.monotonic() + 120
ready = False

while not stopping and time.monotonic() < deadline:
    if web.poll() is not None:
        raise SystemExit(
            f"Web server exited: {web.returncode}"
        )
    try:
        with urllib.request.urlopen(
            "http://127.0.0.1:8000/ready", timeout=2
        ) as response:
            if response.status == 200:
                ready = True
                break
    except (urllib.error.URLError, TimeoutError, OSError):
        time.sleep(2)

if not ready:
    stop_all()
    web.wait(timeout=10)
    raise SystemExit("Web server did not become ready")

bot = subprocess.Popen([sys.executable, "telegram_bot.py"])
print("Web panel ready; Telegram bot started.", flush=True)

try:
    while not stopping:
        if web.poll() is not None:
            print("Web server exited; stopping bot.", flush=True)
            break
        if bot.poll() is not None:
            print("Telegram bot exited; stopping web server.", flush=True)
            break
        time.sleep(2)
finally:
    stop_all()
    for proc in (bot, web):
        if proc is not None and proc.poll() is None:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

raise SystemExit(0 if stopping else 1)
