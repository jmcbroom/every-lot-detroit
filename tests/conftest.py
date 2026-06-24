import os
import sys

# Ensure the project root (where everylot.py et al. live) is importable
# regardless of pytest's import mode.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
