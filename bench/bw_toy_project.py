"""Write the toy Brightway project of `tests/fixtures/brightway`, with bw2data itself.

Run once, with the pins of the Science team's lock file:

```
uv run --no-project --python 3.12 --with bw2data==4.5.4 --with bw2calc==2.3 \
    --with peewee==3.19.0 python bench/bw_toy_project.py tests/fixtures/brightway
```

The project is written into a new temporary `BRIGHTWAY2_DIR`, never your own: importing bw2data
creates and migrates files in whatever folder it uses. Only the files headwater reads are copied
to the fixture folder, after a VACUUM: `projects.db`, and the project's `databases.json`,
`lci/databases.db` and `parameters.db`. Tests read them and never import Brightway.

The project `toy` holds the toy example of docs/calculation.md as six databases:

- `toy-biosphere`: carbon dioxide and methane, fossil, to air.
- `toy-background`: cotton fabric, electricity and the incineration of textile waste, all GLO,
  named as the toy release names them. The incineration makes -1 kg of waste, as ecoinvent does.
- `dyes`: reactive dye production, with the database parameter `kwh_per_kg` = 2 in a formula.
- `tshirt`: the T-shirt, with a project parameter (`scrap_share`, uncertain), a database
  parameter (`fabric_kg`), an activity parameter with a formula (`waste_kg`), formulas on the
  fabric and waste lines, and a lognormal on the electricity line. It scores 1.25205 for the toy
  climate method, and 1.19205 without its waste line.
- `tshirt-dyed`: a T-shirt that buys 0.01 kg of dye from `dyes`. It scores 1.242646.
- `twins`: two activities alike in name, reference product and location, `fabric cutting`, cut
  fabric, GLO, with codes `cut-a` (1 kg of fabric per kg) and `cut-b` (1.2 kg). `T-shirt A` buys
  0.25 kg from `cut-a` and `T-shirt B` from `cut-b`, both with 0.5 kWh. They score
  0.25 × 3.7086 + 0.5 × 0.5298 = 1.19205 and 0.3 × 3.7086 + 0.2649 = 1.37748.

The script checks those scores with bw2calc, within 1e-6, before it copies anything.
"""

import math
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

target = Path(sys.argv[1]).resolve()
scratch = Path(tempfile.mkdtemp(prefix="bw-toy-"))
os.environ["BRIGHTWAY2_DIR"] = str(scratch)

import bw2calc as bc  # noqa: E402
import bw2data as bd  # noqa: E402

PROJECT = "toy"
CH4 = 29.8


def main() -> None:
    bd.projects.set_current(PROJECT)
    bd.Database("toy-biosphere").write(
        {
            ("toy-biosphere", "co2"): {
                "name": "Carbon dioxide, fossil",
                "categories": ("air",),
                "unit": "kilogram",
                "type": "emission",
            },
            ("toy-biosphere", "ch4"): {
                "name": "Methane, fossil",
                "categories": ("air",),
                "unit": "kilogram",
                "type": "emission",
            },
        }
    )
    bd.Database("toy-background").write(
        {
            ("toy-background", "cotton-fabric"): _activity(
                "cotton fabric production",
                "cotton fabric",
                "kilogram",
                ("toy-background", "cotton-fabric"),
                1.0,
                [
                    _tech(("toy-background", "electricity"), 3.0, "kilowatt hour"),
                    _bio("co2", 2.0),
                    _bio("ch4", 0.004),
                ],
            ),
            ("toy-background", "electricity"): _activity(
                "electricity production",
                "electricity",
                "kilowatt hour",
                ("toy-background", "electricity"),
                1.0,
                [_bio("co2", 0.5), _bio("ch4", 0.001)],
            ),
            ("toy-background", "incineration"): _activity(
                "incineration of textile waste",
                "textile waste",
                "kilogram",
                ("toy-background", "incineration"),
                -1.0,
                [_bio("co2", 1.2)],
            ),
        }
    )
    bd.Database("dyes").write(
        {
            ("dyes", "reactive-dye"): _activity(
                "reactive dye production",
                "reactive dye",
                "kilogram",
                ("dyes", "reactive-dye"),
                1.0,
                [
                    _tech(
                        ("toy-background", "electricity"),
                        2.0,
                        "kilowatt hour",
                        formula="kwh_per_kg",
                    ),
                    _bio("co2", 4.0),
                ],
            )
        }
    )
    bd.Database("tshirt").write(
        {
            ("tshirt", "tshirt"): _activity(
                "T-shirt",
                "T-shirt",
                "unit",
                ("tshirt", "tshirt"),
                1.0,
                [
                    _tech(
                        ("toy-background", "cotton-fabric"),
                        0.25,
                        "kilogram",
                        formula="fabric_kg",
                        comment="Fabric in the bill of materials",
                    ),
                    _tech(
                        ("toy-background", "electricity"),
                        0.5,
                        "kilowatt hour",
                        **{"uncertainty type": 2, "loc": math.log(0.5), "scale": math.log(1.1)},
                    ),
                    _tech(
                        ("toy-background", "incineration"),
                        -0.05,
                        "kilogram",
                        formula="-waste_kg",
                        comment="Scraps sent to incineration",
                    ),
                ],
                comment="One T-shirt",
            )
        }
    )
    bd.Database("tshirt-dyed").write(
        {
            ("tshirt-dyed", "dyed-tshirt"): _activity(
                "dyed T-shirt",
                "dyed T-shirt",
                "unit",
                ("tshirt-dyed", "dyed-tshirt"),
                1.0,
                [
                    _tech(("toy-background", "cotton-fabric"), 0.25, "kilogram"),
                    _tech(("toy-background", "electricity"), 0.5, "kilowatt hour"),
                    _tech(("dyes", "reactive-dye"), 0.01, "kilogram"),
                ],
            )
        }
    )

    # Written last, so the exchanges of the databases above keep their ids.
    bd.Database("twins").write(
        {
            ("twins", "cut-a"): _activity(
                "fabric cutting",
                "cut fabric",
                "kilogram",
                ("twins", "cut-a"),
                1.0,
                [_tech(("toy-background", "cotton-fabric"), 1.0, "kilogram")],
            ),
            ("twins", "cut-b"): _activity(
                "fabric cutting",
                "cut fabric",
                "kilogram",
                ("twins", "cut-b"),
                1.0,
                [_tech(("toy-background", "cotton-fabric"), 1.2, "kilogram")],
            ),
            ("twins", "tshirt-a"): _activity(
                "T-shirt A",
                "T-shirt A",
                "unit",
                ("twins", "tshirt-a"),
                1.0,
                [
                    _tech(("twins", "cut-a"), 0.25, "kilogram"),
                    _tech(("toy-background", "electricity"), 0.5, "kilowatt hour"),
                ],
            ),
            ("twins", "tshirt-b"): _activity(
                "T-shirt B",
                "T-shirt B",
                "unit",
                ("twins", "tshirt-b"),
                1.0,
                [
                    _tech(("twins", "cut-b"), 0.25, "kilogram"),
                    _tech(("toy-background", "electricity"), 0.5, "kilowatt hour"),
                ],
            ),
        }
    )

    bd.parameters.new_project_parameters(
        [
            {
                "name": "scrap_share",
                "amount": 0.2,
                "uncertainty type": 3,
                "loc": 0.2,
                "scale": 0.02,
                "comment": "Share of the fabric cut away",
            }
        ]
    )
    bd.parameters.new_database_parameters(
        [{"name": "fabric_kg", "amount": 0.25, "unit": "kilogram"}], "tshirt"
    )
    bd.parameters.new_database_parameters(
        [{"name": "kwh_per_kg", "amount": 2.0, "comment": "Electricity to make 1 kg of dye"}],
        "dyes",
    )
    bd.parameters.new_activity_parameters(
        [
            {
                "name": "waste_kg",
                "formula": "fabric_kg * scrap_share",
                "database": "tshirt",
                "code": "tshirt",
            }
        ],
        "tshirt-making",
    )
    bd.parameters.add_exchanges_to_group("tshirt-making", ("tshirt", "tshirt"))
    bd.parameters.add_exchanges_to_group("dye-making", ("dyes", "reactive-dye"))
    bd.parameters.recalculate()

    method = bd.Method(("toy", "climate"))
    method.register(unit="kg CO2-eq")
    method.write([(("toy-biosphere", "co2"), 1.0), (("toy-biosphere", "ch4"), CH4)])
    for key, expected in (
        (("tshirt", "tshirt"), 1.25205),
        (("tshirt-dyed", "dyed-tshirt"), 1.242646),
        (("dyes", "reactive-dye"), 5.0596),
        (("twins", "tshirt-a"), 1.19205),
        (("twins", "tshirt-b"), 1.37748),
    ):
        lca = bc.LCA({bd.get_node(database=key[0], code=key[1]): 1}, ("toy", "climate"))
        lca.lci()
        lca.lcia()
        # bw2calc builds its matrices in float32: 1e-6 is the tolerance against other engines.
        if not math.isclose(lca.score, expected, rel_tol=1e-6):
            sys.exit(f"{key} scores {lca.score!r} in Brightway, not {expected}")
        print(f"{key}: {lca.score:.9g}")

    folder = Path(bd.projects.dir)
    bd.projects.set_current("default")
    for path in (scratch / "projects.db", folder / "lci" / "databases.db", folder / "parameters.db"):
        with sqlite3.connect(path) as db:
            db.execute("PRAGMA journal_mode=DELETE")
        db = sqlite3.connect(path)
        db.execute("VACUUM")
        db.close()
    if target.exists():
        shutil.rmtree(target)
    (target / folder.name / "lci").mkdir(parents=True)
    shutil.copy(scratch / "projects.db", target / "projects.db")
    for name in ("databases.json", "lci/databases.db", "parameters.db"):
        shutil.copy(folder / name, target / folder.name / name)
    shutil.rmtree(scratch)
    print(f"wrote {target}")


def _activity(name, product, unit, key, amount, exchanges, comment=None):
    data = {
        "name": name,
        "reference product": product,
        "unit": unit,
        "location": "GLO",
        "type": "process",
        "exchanges": [
            {"input": key, "amount": amount, "unit": unit, "type": "production"},
            *exchanges,
        ],
    }
    if comment is not None:
        data["comment"] = comment
    return data


def _tech(key, amount, unit, **fields):
    return {"input": key, "amount": amount, "unit": unit, "type": "technosphere", **fields}


def _bio(code, amount):
    return {
        "input": ("toy-biosphere", code),
        "amount": amount,
        "unit": "kilogram",
        "type": "biosphere",
    }


if __name__ == "__main__":
    main()
