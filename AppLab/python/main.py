"""Display the latest chat received by the Mycelium BLE Brick."""

import json
import time
from pathlib import Path

from arduino.app_utils import App, Bridge

MESSAGE = Path(__file__).with_name(".mycelium-matrix.json")
last_id = None


def show_latest():
    global last_id
    time.sleep(0.2)
    try:
        event = json.loads(MESSAGE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return
    if event["id"] == last_id:
        return

    # ArduinoGraphics' small font uses printable ASCII.
    text = "".join(c if " " <= c <= "~" else "?" for c in event["text"])
    if Bridge.call("mycelium_show_chat", text):
        last_id = event["id"]


App.run(user_loop=show_latest)
