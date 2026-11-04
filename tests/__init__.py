"""Run from the repo root: python -m unittest. Everything runs against a throwaway folder: each test
module sets one before it imports docket, so a run with -s tests can't reach your own list either."""
import os
import tempfile

os.environ["DOCKET_HOME"] = tempfile.mkdtemp(prefix="docket-test-")
