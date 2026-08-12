import os
import tempfile

# Keep import-time side effects of app.main (module-level create_app())
# away from the real home directory during test runs.
os.environ.setdefault("KARAOKE_MM_DATA_DIR", tempfile.mkdtemp(prefix="karaoke-mm-tests-"))
