# History cache for printer jobs
#
# This file may be distributed under the terms of the GNU GPLv3 license.
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

        self.server.register_endpoint(
            "/server/tasks/list", ['GET'], self._handle_tasks_list)

        self.server.register_endpoint(
            "/server/tasks/create", ['GET'], self._add_new_task)

        self.server.register_endpoint(
            "/server/tasks/start", ['GET'], self._start_task)

        database.register_local_namespace(TASK_NAMESPACE)
        self.tasks_ns = database.wrap_namespace(TASK_NAMESPACE,
                                                  parse_keys=False)

        self.current = None
        self.print_stats = {}

    async def _handle_tasks_list(self, web_request):
        savedtasks = self.tasks_ns.values()
        tasks = []
        for task in savedtasks:
            if type(task) == "int":
                continue
            task["metadata"] = self.gcdb[task.filename]
            tasks.append(task)
        return tasks

    async def _add_new_task(self, web_request):
        nextid = self.tasks_ns.get("nextid", 0)

        file = web_request.get_str("file")
        if not self.file_manager.check_file_exists("gcodes", file):
            return {"error": "File does not exist"}

        name, _ = os.path.splitext(os.path.basename(file))
        task = {"id": nextid, "filename": file, "name": name, "status": "created"}
        self.tasks_ns.insert(nextid, task)
        self.tasks_ns.update_child("nextid", nextid + 1)
        return task

    async def _start_task(self, web_request):
        id = web_request.get_int("id", None)
        if id is None:
            return {"error": "No task specified"}
        task = self.get_task(id)
        if task is None:
            return {"error": "Task does not exist"}
        klippy_apis = self.server.lookup_component('klippy_apis')
        klippy_apis.start_print(task["filename"])
        self.current = id


    def get_task(self, id):
        task = self.tasks_ns.get(id)
        if task is None:
            return None
        task["metadata"] = self.gcdb[task["filename"]]

    def set_task_state(self, status):
        task = self.tasks_ns.get(self.current)
        task["status"] = status
        self.tasks_ns.update_child(self.current, task)

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

def load_component(config):
    return Tasks(config)
