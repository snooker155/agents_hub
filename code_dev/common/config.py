from dataclasses import dataclass
import os
from dotenv import load_dotenv

load_dotenv()

# MODEL_NAME = "llama3.1:8b"
# MODEL_NAME = "gpt-oss:20b"
MODEL_NAME = "gpt-4o"

@dataclass
class Paths:
    out: str = "./out"
    docs: str = "./out/docs"
    plan: str = "./out/plan"
    logs: str = "./out/logs"
    code_be: str = "./out/code/backend"
    code_fe: str = "./out/code/frontend"
    ops: str = "./out/ops"
    tests: str = "./out/tests"

@dataclass
class Models:
    pm: str = MODEL_NAME
    ba_json: str = MODEL_NAME
    ba_md: str = MODEL_NAME
    sd: str = MODEL_NAME
    tl: str = MODEL_NAME
    dev: str = MODEL_NAME

@dataclass
class Settings:
    mode_full: bool = True          # full (вопросы/валидации) или simple
    backend_lang_default: str = "python"  # fallback
    openai_api_key: str = os.environ.get("OPENAI_API_KEY", "")
    model: str = os.environ.get("OPENAI_MODEL", "gpt-4o")

settings = Settings()