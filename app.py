"""Streamlit entrypoint for local repo runs.

This wrapper keeps `streamlit run app.py` working in source checkouts,
while the packaged app code lives under `receipt_processor`.
"""

from pathlib import Path
import sys

repo_root = Path(__file__).resolve().parent
src_dir = repo_root / "src"
if src_dir.exists():
    sys.path.insert(0, str(src_dir))

from receipt_processor.streamlit_app import *  # noqa: F401,F403
