"""Local, reproducible benchmarking harness for clinical PHI de-identification models.

Importing this package applies OFFLINE_MODE (default: true) before any Hugging Face
library is imported, so evaluation never touches the network.
"""
from harness.env import apply_offline_mode

apply_offline_mode()

__version__ = "0.1.0"
