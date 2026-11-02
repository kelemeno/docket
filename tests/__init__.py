"""Run from the repo root: python -m unittest. Everything runs against a throwaway folder."""
import os
import tempfile

os.environ["DOCKET_HOME"] = tempfile.mkdtemp(prefix="docket-test-")
