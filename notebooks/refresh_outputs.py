"""Refresh, or check, the kept outputs of the toy notebooks.

Run it from the repository root:

    uv run --locked --group notebooks --group brightway python notebooks/refresh_outputs.py
    uv run --locked --group notebooks --group brightway python notebooks/refresh_outputs.py --check

The first form runs each toy notebook and writes its outputs back. A notebook that fails is not
written. Name notebooks to refresh only those: `refresh_outputs.py 01-first-footprint.ipynb`.
The second form writes nothing: it runs the notebooks and fails when what they print is not what
the files hold, so the outputs cannot go stale. It also fails when a notebook on licensed data
holds an output. CI runs it. `--clear-licensed` removes the outputs of the licensed notebooks.

Notebook 04 runs Brightway, from the `brightway` dependency group. Without it, both forms leave
04 out and say so, as CI does; naming 04 without it is an error.

Both forms remove what changes from run to run (the time of each cell, the Python patch version
and how a cell's text is split into chunks), so a refresh shows only real changes.
"""

import importlib.util
import sys
from pathlib import Path

import nbformat
from nbclient import NotebookClient
from nbformat import NotebookNode

TOY_NOTEBOOKS = [
    "00-getting-started.ipynb",
    "01-first-footprint.ipynb",
    "02-writing-a-database.ipynb",
    "04-coming-from-brightway.ipynb",
]
LICENSED_NOTEBOOKS = [
    "03-working-with-ecoinvent.ipynb",
    "05-brightway-and-headwater-on-ecoinvent.ipynb",
]
"""Notebooks on ecoinvent: their outputs hold licensed values, so they are committed without."""
NEEDS_BRIGHTWAY = {"04-coming-from-brightway.ipynb"}
"""Toy notebooks that run Brightway: checked and refreshed only where it is installed."""


def join_streams(outputs: list) -> list:
    """Join consecutive pieces of the same stream into one."""
    joined = []
    for output in outputs:
        last = joined[-1] if joined else None
        if last and output.output_type == last.output_type == "stream" and output.name == last.name:
            last.text += output.text
        else:
            joined.append(output)
    return joined


def run(path: Path) -> NotebookNode:
    """Run a notebook from its own folder and return it with fresh outputs. Nothing is written."""
    notebook = nbformat.read(path, as_version=4)
    resources = {"metadata": {"path": str(path.parent)}}
    NotebookClient(notebook, timeout=300, record_timing=False, resources=resources).execute()
    for cell in notebook.cells:
        if cell.cell_type == "code":
            cell.outputs = join_streams(cell.outputs)
    notebook.metadata["language_info"] = {"name": "python"}
    return notebook


def printed(notebook: NotebookNode) -> list[list[str]]:
    """What a reader sees under each code cell: its text and its plain-text results."""
    shown = []
    for cell in notebook.cells:
        if cell.cell_type == "code":
            shown.append(
                [
                    output.text if output.output_type == "stream" else output.data["text/plain"]
                    for output in cell.outputs
                    if output.output_type == "stream" or "text/plain" in output.get("data", {})
                ]
            )
    return shown


def with_outputs(path: Path) -> bool:
    """True when a code cell of the notebook holds an output or an execution count."""
    notebook = nbformat.read(path, as_version=4)
    return any(
        cell.outputs or cell.execution_count is not None
        for cell in notebook.cells
        if cell.cell_type == "code"
    )


def clear(path: Path) -> None:
    """Remove every output and execution count of the notebook, and write it back."""
    notebook = nbformat.read(path, as_version=4)
    for cell in notebook.cells:
        if cell.cell_type == "code":
            cell.outputs = []
            cell.execution_count = None
    nbformat.write(notebook, path)


def main() -> None:
    here = Path(__file__).parent
    args = sys.argv[1:]
    named = [a for a in args if not a.startswith("--")]
    unknown = sorted(set(named) - set(TOY_NOTEBOOKS))
    if unknown:
        sys.exit(f"Not a toy notebook: {', '.join(unknown)}. Toy notebooks: {TOY_NOTEBOOKS}")
    names = named or TOY_NOTEBOOKS
    if importlib.util.find_spec("bw2data") is None:
        if set(named) & NEEDS_BRIGHTWAY:
            sys.exit(
                "Notebook 04 needs Brightway: uv sync --group notebooks --group brightway, "
                "then run this again."
            )
        left_out = [name for name in names if name in NEEDS_BRIGHTWAY]
        if left_out:
            print(f"Left out {', '.join(left_out)}: Brightway is not installed.")
        names = [name for name in names if name not in NEEDS_BRIGHTWAY]
    paths = [here / name for name in names]
    if "--clear-licensed" in args:
        for name in LICENSED_NOTEBOOKS:
            clear(here / name)
        print(f"Cleared the outputs of {', '.join(LICENSED_NOTEBOOKS)}.")
        return
    if "--check" in args:
        licensed = [name for name in LICENSED_NOTEBOOKS if with_outputs(here / name)]
        if licensed:
            sys.exit(
                f"{', '.join(licensed)} holds outputs, which are ecoinvent values. Clear them "
                "with notebooks/refresh_outputs.py --clear-licensed, or Clear All Outputs in "
                "your editor, before you commit."
            )
        stale = [p.name for p in paths if printed(run(p)) != printed(nbformat.read(p, 4))]
        if stale:
            sys.exit(
                f"The outputs of {', '.join(stale)} are stale. Run notebooks/refresh_outputs.py."
            )
        print("The outputs of the toy notebooks are current, and the licensed ones have none.")
        return
    for path in paths:
        nbformat.write(run(path), path)


if __name__ == "__main__":
    main()
