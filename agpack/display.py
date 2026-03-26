"""Rich console and progress display helpers."""

from __future__ import annotations

from rich.console import Console
from rich.progress import Progress
from rich.progress import ProgressColumn
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

    def render(self, task: object) -> Text:  # noqa: ANN001 – Rich Task
        if task.finished:  # type: ignore[union-attr]
            icon = task.fields.get("icon", " ")  # type: ignore[union-attr]
            return Text.from_markup(icon)
        return self._spinner.render(task.get_time())  # type: ignore[union-attr]


def create_sync_progress() -> Progress:
    """Create a Progress instance styled for agpack sync."""
    return Progress(
        StatusColumn(),
        TextColumn("{task.description}"),
        TextColumn("{task.fields[detail]}", style="dim"),
        console=console,
    )
