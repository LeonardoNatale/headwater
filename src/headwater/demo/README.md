# Demo databases and study

A repository of databases and a study in miniature, on a toy background, so it needs no licence. `headwater demo FOLDER` copies it into FOLDER. In the headwater repository it is also the demo of the results diff in CI ([CI](https://github.com/LeonardoNatale/headwater/blob/main/docs/workflows.md#ci)).

Try it, in the folder:

```bash
headwater release pull releases/toy-background@6864b7dbb65d
headwater validate studies/tshirt-footprint
headwater calc studies/tshirt-footprint
headwater contributions studies/tshirt-dyed --indicator climate-change
```

| Path | What it is |
|---|---|
| `studies/tshirt` | A database: the T-shirt of [docs/calculation.md](https://github.com/LeonardoNatale/headwater/blob/main/docs/calculation.md), 0.25 kg fabric and 0.5 kWh. Score 1.19205. |
| `studies/tshirt-local-electricity` | A database: the same T-shirt with an override, electricity CO2 0.5 → 0.3 kg/kWh for its own purchase only. Score 1.09205. |
| `studies/dyes` | A database: 1 kg of reactive dye takes 2 kWh and emits 4 kg CO2. Score 5.0596. |
| `studies/tshirt-dyed` | A database: the T-shirt plus 0.01 kg of dye from `dyes`, `uses: [dyes]`. Score 1.19205 + 0.01 × 5.0596 = 1.242646. |
| `studies/tshirt-footprint` | A study: 1 T-shirt of `tshirt-dyed`, 1.242646, and its scenario `cleaner-dye`, the dye at 3.5 kg CO2, 1.237646. It holds no processes. |
| `releases/toy-background@6864b7dbb65d` | The toy background and the toy climate method as a frozen release. Every database pins it with `background: toy-background@6864b7dbb65d`. |

In git, a change shows as a diff of scores. Example: commit the folder in a git repository, lower the dye's CO2 from 4 to 3.5 kg per kg on line e03 of `studies/dyes/processes/reactive-dye.csv`, and run `headwater diff HEAD studies/tshirt-footprint`. The T-shirt goes from 1.24265 to 1.23765 kg CO2-eq, and line e03 is named as the cause. On a pull request in the headwater repository, the CI comment lists the database `dyes` (5.0596 → 4.5596), then `tshirt-dyed`, which uses it, then the study.

In the repository, `tests/ci/build_demo_release.py` writes the release from the toy fixtures, and a test fails when its hash or the pin of a database differs.
