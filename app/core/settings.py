import os
from dotenv import load_dotenv

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(ROOT_DIR, ".env"))

DEFAULT_MODEL = "anthropic:claude-sonnet-4-5-20250929"
CHECKPOINT_DB = os.path.join(ROOT_DIR, "data", "checkpoints.db")
os.makedirs(os.path.dirname(CHECKPOINT_DB), exist_ok=True)
