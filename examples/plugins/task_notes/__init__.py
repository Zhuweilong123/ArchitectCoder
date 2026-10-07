"""Small external plugin demonstrating directory discovery and phase contribution."""


class TaskNotes:
    def __init__(self, label):
        self.label = label

    def describe(self):
        return self.label


def create(*, settings, **kwargs):
    return TaskNotes(settings.plugin_task_notes_label)


def observe_start(context):
    from app.agent_base.host_api.services import get_host_services
    get_host_services().record_event("plugin_example", plugin="task_notes", stage=context.event.value,
                   run_id=context.run_id, message="Task notes observed the run start")
