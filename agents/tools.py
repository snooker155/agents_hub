from __future__ import annotations
import os
import json
import time
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from langchain_core.tools import tool, StructuredTool
from common.config import Paths
from common.utils import load_json, write

class AskUserInputInput(BaseModel):
    questions: List[str] = Field(..., description="List of questions to ask the user")

@tool("ask_user_input", args_schema=AskUserInputInput)
def ask_user_input(questions: List[str]) -> str:
    """Ask the user for clarifying questions and wait for answers via the dashboard."""
    P = Paths()
    input_path = os.path.join(P.logs, "pending_input.json")
    waiting_path = os.path.join(P.logs, "waiting_for_input.json")

    # Write what we are waiting for
    write(waiting_path, json.dumps({"questions": questions}, ensure_ascii=False))

    print(f"[Agent] Waiting for user input via dashboard...")
    # In a real environment, we'd probably have a better way to wait,
    # but for this simulation, we'll wait for the file to appear.
    # To avoid infinite loop in some environments, let's add a timeout.
    timeout = 300 # 5 minutes
    start_time = time.time()

    while not os.path.exists(input_path):
        if time.time() - start_time > timeout:
            return "Timeout waiting for user input."
        time.sleep(2)

    answers = load_json(input_path)

    # Cleanup
    if os.path.exists(input_path): os.remove(input_path)
    if os.path.exists(waiting_path): os.remove(waiting_path)

    return json.dumps(answers, ensure_ascii=False, indent=2)

class CreateTaskInput(BaseModel):
    title: str = Field(..., description="Task title")
    assignee: str = Field(..., description="Assignee role (e.g., BA, SD, BE, FE)")
    description: str = Field("", description="Task description")
    parent_id: Optional[str] = Field(None, description="Parent task ID")

@tool("create_factory_task", args_schema=CreateTaskInput)
def create_factory_task(title: str, assignee: str, description: str = "", parent_id: Optional[str] = None) -> str:
    """Create a new task in the factory task registry."""
    from common.tasks import Task, load_tasks, save_tasks, next_id
    tasks = load_tasks()
    tid = next_id(tasks, f"TASK-{assignee}")
    t = Task(id=tid, title=title, assignee=assignee, description=description, parent_id=parent_id)
    tasks.append(t)
    save_tasks(tasks)
    return json.dumps(t.model_dump(), ensure_ascii=False)

def get_factory_tools() -> List[Any]:
    return [ask_user_input, create_factory_task]
