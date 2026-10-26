"""Start the app:  python run.py   then open the address it prints (normally http://localhost:8000)"""

import os
import socket
import sys
import webbrowser
from threading import Timer

import uvicorn


def free_port(preferred: int) -> int:
    """Use the preferred port, or the next free one if another app (e.g. another project) already has it."""
    for port in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    sys.exit(f"Ports {preferred}-{preferred + 19} are all busy. Set PORT=<number> and try again.")


if __name__ == "__main__":
    port = free_port(int(os.getenv("PORT", "8000")))
    url = f"http://localhost:{port}"
    print(f"\n  Encore is running at {url}  (Ctrl+C to stop)\n", flush=True)
    if not os.getenv("NO_BROWSER"):
        Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run("app.server:app", host="127.0.0.1", port=port)
