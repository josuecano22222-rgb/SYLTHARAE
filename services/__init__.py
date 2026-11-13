"""Unified service layer (frontend + CLI compatibility share these).

Nothing in this package may depend on argparse, terminal output, input(),
sys.argv, or process exit codes. Pages and CLIs are thin adapters on top.
"""
