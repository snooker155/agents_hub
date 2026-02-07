from dataclasses import dataclass, field
import os
from dotenv import load_dotenv

load_dotenv()

# MODEL_NAME = "llama3.1:8b"
# MODEL_NAME = "gpt-oss:20b"
MODEL_NAME = "gpt-4o"

@dataclass
class Paths:
    out: str = os.environ.get("WORKSPACE_ROOT", "./out")
    docs: str = field(init=False)
    plan: str = field(init=False)
    logs: str = field(init=False)
    code_be: str = field(init=False)
    code_fe: str = field(init=False)
    ops: str = field(init=False)
    tests: str = field(init=False)

    def __post_init__(self):
        self.docs = os.path.join(self.out, "docs")
        self.plan = os.path.join(self.out, "plan")
        self.logs = os.path.join(self.out, "logs")
        self.code_be = os.path.join(self.out, "code/backend")
        self.code_fe = os.path.join(self.out, "code/frontend")
        self.ops = os.path.join(self.out, "ops")
        self.tests = os.path.join(self.out, "tests")

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
