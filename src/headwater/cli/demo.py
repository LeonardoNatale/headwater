"""`headwater demo`: copy the demo that ships in the package into a folder."""

import shutil
from importlib.resources import as_file, files
from pathlib import Path
from typing import Annotated

import typer

from headwater.cli._common import fail


def demo_command(
    folder: Annotated[
        Path,
        typer.Argument(help="Where to copy the demo: a new or an empty folder, `.git` allowed."),
    ],
) -> None:
    """Copy the demo into FOLDER: a toy release, four databases and a study of one of them.

    The data is a toy, so it needs no licence: a T-shirt of cotton fabric, electricity and dye, on
    a background of three processes. Then, in FOLDER, `headwater release pull` puts the release in
    the cache and `headwater calc studies/tshirt-footprint` scores the study. The folder's
    README.md says what each part is.
    """
    if folder.exists() and not folder.is_dir():
        fail(f"{folder} is not a folder", code=2)
    # Hidden entries, such as `.git`, may be there: the demo has none, so nothing is overwritten.
    if folder.is_dir() and any(not p.name.startswith(".") for p in folder.iterdir()):
        fail(f"{folder} is not empty: give a new or an empty folder")
    with as_file(files("headwater") / "demo") as demo:
        shutil.copytree(demo, folder, dirs_exist_ok=True)
    [release] = (p.name for p in (folder / "releases").iterdir())
    typer.echo(
        f"Copied the demo into {folder}. Then, in that folder:\n\n"
        f"  headwater release pull {Path('releases') / release}\n"
        "  headwater calc studies/tshirt-footprint"
    )
