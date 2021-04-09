# History cache for printer jobs
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging
import time

TASK_NAMESPACE = "tasks"


class Tasks:
    def __init__(self, config):
        self.server = config.get_server()
        self.file_manager = self.server.lookup_component('file_manager')
        database = self.server.lookup_component("database")
        self.gcdb = database.wrap_namespace("gcode_metadata", parse_keys=False)

        self.server.register_endpoint(
            "/server/tasks/list", ['GET'], self._handle_tasks_list)

        database.register_local_namespace(TASK_NAMESPACE)
        self.tasks_ns = database.wrap_namespace(TASK_NAMESPACE,
                                                  parse_keys=False)

    async def _handle_tasks_list(self, web_request):
        return {"status": "success"}


def load_component(config):
    return Tasks(config)
