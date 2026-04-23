"""Streamlit entrypoint for local repo runs.

This wrapper keeps `streamlit run app.py` working in source checkouts,
while the packaged app code lives under `receipt_processor`.
"""

from receipt_processor.streamlit_app import *  # noqa: F401,F403

