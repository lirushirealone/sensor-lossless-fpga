"""Reuse qualified exact integer import and full verification."""
from desktop_gui_v1.dataio import *
from desktop_gui_v1 import dataio as qualified

def load(path):
    try:return qualified.load(path)
    except ValueError as exc:
        message=str(exc).replace('64MiB','67.11 MB')
        if message!=str(exc):raise ValueError(message) from exc
        raise
