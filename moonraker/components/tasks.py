# History cache for printer jobs
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import json
import logging
import time
import os

TASK_NAMESPACE = "tasks"


class Tasks:
    def __init__(self, config):
        self.server = config.get_server()
        self.file_manager = self.server.lookup_component('file_manager')
        database = self.server.lookup_component("database")
        self.gcdb = database.wrap_namespace("gcode_metadata", parse_keys=False)

        self.server.register_event_handler(
            "server:klippy_ready", self._init_ready)
        self.server.register_event_handler(
            "server:status_update", self._status_update)
        self.server.register_event_handler(
            "server:klippy_disconnect", self._handle_disconnect)
        self.server.register_event_handler(
            "server:klippy_shutdown", self._handle_shutdown)

        self.server.register_event_handler(
            "history:history_changed", self._handle_history)

        self.server.register_endpoint(
            "/server/tasks/list", ['GET'], self._handle_tasks_list)

        self.server.register_endpoint(
            "/server/tasks/create", ['GET'], self._handle_create_task)

        self.server.register_endpoint(
            "/server/tasks/start", ['GET'], self._start_task)

        self.server.register_endpoint(
            "/server/tasks/current", ['GET'], self._handle_current_task)

        database.register_local_namespace(TASK_NAMESPACE)
        self.tasks_ns = database.wrap_namespace(TASK_NAMESPACE,
                                                  parse_keys=False)

        self.current = None
        self.print_stats = {}

        if self.tasks_ns.get("tasks") is None:
            self.tasks_ns["tasks"] = {}

    async def _handle_tasks_list(self, web_request):
        savedtasks = self.tasks_ns.get("tasks")
        if savedtasks is None:
            return []
        tasks = []
        for task in savedtasks.values():
            task["metadata"] = self.gcdb.get(task["filename"])
            tasks.append(task)
        return tasks

    async def _handle_current_task(self, web_request):
        if self.current is None:
            return None
        else:
            task = self.get_task(self.current)
            if task is None:
                return task
            return task.to_dict()

    async def _handle_create_task(self, web_request):
        taskid = self.tasks_ns.get("nextid", 0)

        file = web_request.get_str("file")
        if not self.file_manager.check_file_exists("gcodes", file):
            return {"error": "File does not exist"}

        name, _ = os.path.splitext(os.path.basename(file))

        task = PrinterTask()
        task.filename = file
        task.name = name
        task.created_time = time.time()
        task.task_id = f"{taskid:06}"
        task.status = "created"

        self.save_task(task)
        self.tasks_ns["nextid"] = taskid + 1
        return task.to_dict()

    async def _start_task(self, web_request):
        taskid = web_request.get_int("id", None)
        if taskid is None:
            return {"error": "No task specified"}

        if type(taskid) == int:
            taskid = f"{taskid:06}"

        task = self.get_task(taskid)
        if task is None:
            return {"error": "Task does not exist"}
        klippy_apis = self.server.lookup_component('klippy_apis')
        await klippy_apis.start_print(task.filename)
        self.current = taskid


    def get_task(self, taskid):
        if type(taskid) == int:
            taskid = f"{taskid:06}"

        task = self.tasks_ns["tasks"].get(taskid)
        if task is None:
            return None
        task = PrinterTask(task)
        task.metadata = self.gcdb.get(task.filename)
        return task

    def save_task(self, task):
        tasks = self.tasks_ns.get("tasks") or {}
        tasks[task.task_id] = task.to_dict()
        self.tasks_ns["tasks"] = tasks

    def set_task_state(self, status):
        task = self.get_task(self.current)
        task.status = status
        self.save_task(task)

    def finish_task(self, status):
        self.set_task_state(status)
        self.current = None

    async def _init_ready(self):
        klippy_apis = self.server.lookup_component('klippy_apis')
        sub = {"print_stats": None}
        try:
            result = await klippy_apis.subscribe_objects(sub)
        except self.server.error as e:
            logging.info(f"Error subscribing to print_stats")
        self.print_stats = result.get("print_stats", {})

    async def _status_update(self, data):
        ps = data.get("print_stats", {})
        if "state" in ps:
            old_state = self.print_stats['state']
            new_state = ps['state']
            new_ps = dict(self.print_stats)
            new_ps.update(ps)

            if new_state is not old_state:
                if new_state == "printing" and self.current is not None:
                    self.set_task_state("printing")
                elif self.current is not None:
                    if new_state == "complete":
                        self.finish_task("completed")
                    if new_state == "standby":
                        self.finish_task("cancelled")
                    elif new_state == "error":
                        self.finish_task("error")

    def _handle_shutdown(self):
        self.finish_task("klippy_shutdown")

    def _handle_disconnect(self):
        self.finish_task("klippy_disconnect")

    def _handle_history(self, event):
        if event["action"] == "added":
            if self.current:
                task = self.get_task(self.current)
                job_id = event["job"]["job_id"]
                task.last_job_id = job_id
                task.jobs.append(job_id)
                self.save_task(task)


class PrinterTask:
    def __init__(self, data={}):
        self.task_id = data.get("task_id")
        self.created_time = data.get("created_time") or time.time()
        self.filename = data.get("filename")
        self.name = data.get("name")
        self.metadata = data.get("metadata")
        self.status = data.get("status")
        self.update_from_ps(data)
        self.last_job_id = None
        self.jobs = []

    def get(self, name):
        if not hasattr(self, name):
            return None
        return getattr(self, name)

    def to_dict(self):
        return self.__dict__.copy()

    def set(self, name, val):
        if not hasattr(self, name):
            return
        setattr(self, name, val)

    def update_from_ps(self, data):
        for i in data:
            if hasattr(self, i):
                setattr(self, i, data[i])


def load_component(config):
    return Tasks(config)
