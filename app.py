"""Streamlit entrypoint for local repo runs.

This wrapper keeps `streamlit run app.py` working in source checkouts,
while the packaged app code lives under `receipt_processor`.
"""

from pathlib import Path
import sys

repo_root = Path(__file__).resolve().parent
src_dir = repo_root / "src"
if src_dir.exists() and str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

# If an unrelated installed `receipt_processor` package was imported earlier
# in this interpreter, drop it so imports resolve to this repo's local source.
loaded_pkg = sys.modules.get("receipt_processor")
if loaded_pkg is not None:
    loaded_path = getattr(loaded_pkg, "__file__", None)
    if loaded_path:
        try:
            loaded_file = Path(loaded_path).resolve()
            if not loaded_file.is_relative_to(src_dir.resolve()):
                del sys.modules["receipt_processor"]
        except OSError:
            pass

from receipt_processor.streamlit_app import *  # noqa: F401,F403
