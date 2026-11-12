"""The fingerprint of a calculation's inputs: the same records always give the same hash."""

from typing import Any

from headwater.release.content import canonical_json, sha256
from headwater.study import DatabaseFolder, ProcessFolder


def sha256_of(data: Any) -> str:
    """sha256 of `data` written as canonical JSON: sorted keys, no spaces, no NaN."""
    return sha256(canonical_json(data))


def records_of(folder: ProcessFolder) -> dict[str, Any]:
    """The records of a folder, sorted by id, so the order of lines in a file does not matter.

    For a database, also its parameters, overrides, cut-offs (with their reasons and shares) and
    the rows of its no_factor.csv: everything it says about its processes changes the hash. Its
    scenarios do not: a result counts the scenario it was calculated in, and only that one. A
    no_factor.csv with no rows hashes the same as no file.
    """
    processes = [
        {
            **p.model_dump(mode="json", exclude={"exchanges"}),
            "exchanges": [
                e.model_dump(mode="json") for e in sorted(p.exchanges, key=lambda e: e.line_id)
            ],
        }
        for p in sorted(folder.processes, key=lambda p: p.id)
    ]
    database: dict[str, Any] = {}
    if isinstance(folder, DatabaseFolder):
        database = {
            "parameters": [
                p.model_dump(mode="json") for p in sorted(folder.parameters, key=lambda p: p.id)
            ],
            "overrides": [
                {
                    "process_id": o.process_id,
                    "lines": [
                        e.model_dump(mode="json") for e in sorted(o.lines, key=lambda e: e.line_id)
                    ],
                }
                for o in sorted(folder.overrides, key=lambda o: o.process_id)
            ],
            "cut_offs": [
                i.model_dump(mode="json")
                for i in sorted(folder.cut_offs, key=lambda i: (i.process_id, i.line_id))
            ],
        }
        if folder.no_factor:
            database["no_factor"] = [
                r.model_dump(mode="json")
                for r in sorted(folder.no_factor, key=lambda r: (r.flow_id, r.method))
            ]
    return {
        "namespace": folder.namespace,
        "processes": processes,
        "flows": [f.model_dump(mode="json") for f in sorted(folder.flows, key=lambda f: f.id)],
        "flow_properties": [
            x.model_dump(mode="json")
            for x in sorted(folder.flow_properties, key=lambda x: (x.flow_id, x.property.value))
        ],
        "units": [u.model_dump(mode="json") for u in sorted(folder.units, key=lambda u: u.name)],
        **database,
    }
