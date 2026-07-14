"""
This module returns an absolute path to a bundled resource.
Works both when running from source and from a PyInstaller executable.
"""
import os
import sys

def resource_path(*parts):
    if getattr(sys, "frozen", False):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.dirname(__file__))

    return os.path.join(base, *parts)