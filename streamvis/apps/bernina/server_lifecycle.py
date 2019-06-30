from functools import partial
from threading import Thread
from streamvis import receiver
import streamvis as sv


def on_server_loaded(_server_context):
    """This function is called when the server first starts."""
    receiver_start = partial(receiver.current.start, sv.connection_mode, sv.address)
    t = Thread(target=receiver_start, daemon=True)
    t.start()
