"""Rich console and progress display helpers."""

from __future__ import annotations

from rich.console import Console
from rich.console import RenderableType
from rich.progress import Progress
from rich.progress import ProgressColumn
from rich.progress import Task
from rich.progress import TextColumn
from rich.spinner import Spinner
from rich.text import Text

console = Console()


class StatusColumn(ProgressColumn):
    """Spinner while a task is running, status icon when finished.

    The icon is read from the task's ``icon`` field so each task can
    show a different result (e.g. green checkmark vs red cross).
    """

    def __init__(self) -> None:
        super().__init__()
        self._spinner = Spinner("dots")

    def render(self, task: Task) -> RenderableType:
        if task.finished:
            icon: str = task.fields.get("icon", " ")
            return Text.from_markup(icon)
        return self._spinner.render(task.get_time())


def create_sync_progress() -> Progress:
    """Create a Progress instance styled for agpack sync."""
    return Progress(
        StatusColumn(),
        TextColumn("{task.description}"),
        TextColumn("{task.fields[detail]}", style="dim"),
        console=console,
    )
