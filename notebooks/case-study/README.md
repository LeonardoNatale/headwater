# Case study: a backpack

The data of [03-case-study](../03-case-study.ipynb): one backpack, sewn in Vietnam and sold in Europe, on a small background of its own.

**Every number here is invented.** Every amount and every factor was made up for this example and rounded. None comes from ecoinvent or any other licensed source, and none describes a real product, factory or country. The country codes VN and PT only give the example two places.

| Path | What it is |
|---|---|
| `background/` | The background: 20 processes, ids `invented:...`, in the same two tables as any database, `processes.csv` and `exchanges.csv`, with `flows.csv`. Electricity in VN and PT, heat from natural gas, PET granulate (virgin and recycled), polyester fabric (virgin and recycled), nylon granulate and webbing, zinc alloy, zips, polypropylene granulate, buckles, polyurethane foam, cardboard, LDPE film, ship and lorry transport, incineration and landfill. |
| `method/` | The method `invented-climate`: one indicator, climate change, with factors for CO2 (1), methane (29.8) and nitrous oxide (273). |
| `releases/invented-background@9a8f8d0651d9` | The background and the method frozen as a release. The backpack database pins it. A Monte Carlo run needs a release, not a folder. |
| `project/backpack` | A database: the backpack (delivered and disposed of), and its sewing in a factory in Vietnam (`sewing-vn`) and in Portugal (`sewing-pt`). |
| `project/backpack-footprint` | A study: 1 backpack, cradle to grave, 11.454848 kg CO2-eq, and two scenarios: `factory-in-portugal` (10.957088) and `recycled-polyester` (10.0589744). |

Try it from this folder:

```bash
headwater release pull releases/invented-background@9a8f8d0651d9
cd project
headwater calc backpack-footprint
headwater contributions backpack-footprint
headwater calc backpack-footprint --scenario recycled-polyester
```

In the repository, `tests/ci/build_demo_release.py` writes the release from `background/` and `method/`, and a test fails when its hash or the pin of the database differs. The folder is not in the package.
