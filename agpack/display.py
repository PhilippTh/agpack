"""Rich console and progress display helpers."""

from __future__ import annotations

from rich.console import Console
from rich.progress import Progress
from rich.progress import SpinnerColumn
from rich.progress import TextColumn

console = Console()


def create_sync_progress() -> Progress:
    """Create a Progress instance styled for agpack sync."""
    return Progress(
        SpinnerColumn(finished_text=""),
        TextColumn("{task.fields[icon]}"),
        TextColumn("{task.description}"),
        TextColumn("{task.fields[detail]}", style="dim"),
        console=console,
    )
