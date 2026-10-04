import type { Transport } from "./transport.ts";
import type { TaskCreate, TaskDetail, TaskListItem, TaskPage, TaskUpdate } from "./generated/types.ts";

export interface TaskListParams {
  workspace?: string;
  limit?: number;
  offset?: number;
}

/** `tasks.list()`: `TaskListItem[]` with no paging, `TaskPage` with either `limit` or `offset` set. */
export type TaskList = TaskListItem[] | TaskPage;

/** `GET/POST /api/tasks`, `GET/PATCH/DELETE /api/tasks/{task_id}` (dashboard/backend/routes/tasks.py). */
export class TasksResource {
  private readonly transport: Transport;

  constructor(transport: Transport) {
    this.transport = transport;
  }

  list(params: TaskListParams = {}): Promise<TaskList> {
    return this.transport.request("get", "/api/tasks", { query: { ...params } });
  }

  create(task: TaskCreate): Promise<unknown> {
    return this.transport.request("post", "/api/tasks", { body: task });
  }

  get(taskId: string): Promise<TaskDetail> {
    return this.transport.request("get", "/api/tasks/{task_id}", { params: { task_id: taskId } });
  }

  update(taskId: string, patch: TaskUpdate): Promise<unknown> {
    return this.transport.request("patch", "/api/tasks/{task_id}", {
      params: { task_id: taskId },
      body: patch,
    });
  }

  delete(taskId: string): Promise<unknown> {
    return this.transport.request("delete", "/api/tasks/{task_id}", { params: { task_id: taskId } });
  }
}

export type { TaskCreate, TaskDetail, TaskListItem, TaskPage, TaskUpdate };
