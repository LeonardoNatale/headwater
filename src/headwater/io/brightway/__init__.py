"""Import a database of a Brightway project into a database folder, from the project's own
files.

Example: `import_brightway("toy", "tshirt", release, "studies/tshirt")` reads the database
`tshirt` of the Brightway project `toy`, links every line to the node its exchange comes from,
writes the database folder and validates it. bw2data is not needed: the project's SQLite files are
read directly, read-only (see `project`).
"""

from headwater.io.brightway.importer import import_brightway
from headwater.io.brightway.project import BrightwayProject, default_dir
from headwater.io.brightway.reader import ProjectDatabase, read_database

__all__ = [
    "BrightwayProject",
    "ProjectDatabase",
    "default_dir",
    "import_brightway",
    "read_database",
]
