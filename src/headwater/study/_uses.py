"""The databases a database uses: the folders next to it that its `uses:` names, and what they use.

Example: `tshirt` says `uses: [dyes]`, and `dyes` says `uses: [chemicals]`. Reading `tshirt` reads
`../dyes` and `../chemicals`, each once, so the T-shirt's calculation has the processes of all
three.
"""

from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path

from headwater.model import InvalidDataError, Pin, Problem
from headwater.study.folders import DatabaseFolder, in_used

type Sibling = Callable[[str], Path | None]
"""The folder of the database of this name next to the one read, or None when there is none."""

type ReadOne = Callable[[Path], DatabaseFolder]
"""Read one database folder, without the databases it uses."""


def on_disk(root: Path) -> Sibling:
    """The folders next to `root` on disk that hold a `database.yaml`."""
    parent = root.resolve().parent

    def sibling(name: str) -> Path | None:
        folder = parent / name
        return folder if (folder / "database.yaml").is_file() else None

    return sibling


def read_used(
    folder: DatabaseFolder, folder_name: str, sibling: Sibling, read_one: ReadOne
) -> tuple[DatabaseFolder, ...]:
    """Every database `folder` uses, directly or not, each read once, sorted by name. `folder_name`
    is the name of `folder`'s own folder.

    Raises `InvalidDataError` naming every problem: a database that is not there, one whose name is
    not the name of its folder, one that pins another background, a cycle, and the problems of
    a used database that does not read, at their place in its folder (`../dyes/...`).
    """
    problems: list[Problem] = []
    found: dict[str, DatabaseFolder] = {}
    root = folder.database

    def visit(user: DatabaseFolder, path: tuple[str, ...]) -> None:
        here = "database.yaml" if len(path) == 1 else in_used(path[-1], "database.yaml")
        for name in user.database.uses:
            if name in path:
                cycle = " → ".join((*path[path.index(name) :], name))
                problems.append(
                    Problem(
                        "uses-cycle",
                        f"database {user.database.name}",
                        f"uses {name}, which makes a cycle: {cycle}. Databases cannot use each "
                        "other: move what they share into a database that both use.",
                        location=here,
                    )
                )
                continue
            if name in found:
                continue
            used = _read(name, user, here, sibling, read_one, problems)
            if used is None:
                continue
            if _checked(used, name, root.name, folder_name, root.background, problems):
                found[name] = used
                visit(used, (*path, name))

    visit(folder, (folder_name,))
    if problems:
        raise InvalidDataError(dict.fromkeys(problems))
    return tuple(found[name] for name in sorted(found))


def _read(
    name: str,
    user: DatabaseFolder,
    here: str,
    sibling: Sibling,
    read_one: ReadOne,
    problems: list[Problem],
) -> DatabaseFolder | None:
    path = sibling(name)
    if path is None:
        problems.append(
            Problem(
                "used-database-missing",
                f"database {user.database.name}",
                f"uses {name}, but there is no database folder {name} next to it, with a "
                "database.yaml. An entry of uses is the name of a folder beside the database's "
                "own.",
                location=here,
            )
        )
        return None
    try:
        return read_one(path)
    except InvalidDataError as error:
        problems.extend(replace(p, location=in_used(name, p.location)) for p in error.problems)
        return None


def _checked(
    used: DatabaseFolder,
    name: str,
    root_name: str,
    root_folder: str,
    background: Pin,
    problems: list[Problem],
) -> bool:
    """False when the used database cannot be part of the calculation: its name is not its
    folder's, it has the name of the database that uses it, or it pins another background."""
    database = used.database
    where = in_used(name, "database.yaml")
    before = len(problems)
    if database.name != name:
        problems.append(
            Problem(
                "used-database-name",
                f"database {name}",
                f"the folder {name} holds the database {database.name!r}. A database that "
                "another uses "
                f"has the name of its folder, so its process ids are {name}:<file name>: write "
                f"name: {name} in its database.yaml, or rename the folder.",
                location=where,
            )
        )
    elif name == root_name:
        problems.append(
            Problem(
                "used-database-name",
                f"database {name}",
                f"has the name of the database that uses it, {root_name} (in the folder "
                f"{root_folder}), so their process ids would clash. Rename one of them.",
                location=where,
            )
        )
    if database.background != background:
        problems.append(
            Problem(
                "uses-background",
                f"database {name}",
                f"pins background {database.background}, and the database that uses it, "
                f"{root_name}, "
                f"pins {background}. Every database of one calculation is calculated on one "
                "background, so they pin the same release, written the same way.",
                location=where,
            )
        )
    return len(problems) == before


type Uses = Mapping[str, tuple[str, ...] | InvalidDataError]
"""What each database folder of one parent folder uses, by folder name, or why its
`database.yaml` does not say."""


def uses_on_disk(
    folder: Path, read_uses: Callable[[Path], tuple[str, ...]]
) -> dict[str, tuple[str, ...] | InvalidDataError]:
    """What each database folder in `folder` uses: the folders in it that hold a `database.yaml`."""
    found: dict[str, tuple[str, ...] | InvalidDataError] = {}
    if not folder.is_dir():
        return found
    for entry in sorted(folder.iterdir()):
        if (entry / "database.yaml").is_file():
            try:
                found[entry.name] = read_uses(entry)
            except InvalidDataError as error:
                found[entry.name] = error
    return found


def users_of(name: str, uses: Uses) -> list[tuple[str, tuple[str, ...]]]:
    """The databases of `uses` that use the database `name`, directly or not, by name, each with the
    databases it goes through, from the one it names in its own `uses` down to `name`.

    Example: when `tshirt` uses `dyes` and `dyes` uses `chemicals`, the users of `chemicals` are
    `dyes` through `(chemicals,)` and `tshirt` through `(dyes, chemicals)`. Each user is listed
    once, with its shortest way. A database whose `database.yaml` does not read uses nothing here.
    """
    by_used: dict[str, list[str]] = {}
    for user, names in uses.items():
        if isinstance(names, InvalidDataError):
            continue
        for used in names:
            by_used.setdefault(used, []).append(user)
    through: dict[str, tuple[str, ...]] = {}
    queue: list[tuple[str, tuple[str, ...]]] = [(name, (name,))]
    while queue:
        current, chain = queue.pop(0)
        for user in sorted(by_used.get(current, ())):
            if user == name or user in through:
                continue
            through[user] = chain
            queue.append((user, (user, *chain)))
    return sorted(through.items())
