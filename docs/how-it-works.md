# How headwater works

headwater rests on three ideas. This page shows each one on a toy T-shirt, the one the demo ships. [calculation.md](calculation.md) has the math and [design.md](design.md) the file formats. The words are in the [glossary](appendix/glossary.md).

## One T-shirt

A factory makes T-shirts. Each one buys 0.25 kg of cotton fabric and 0.5 kWh of electricity. Its suppliers are in a background of three processes:

| Process | Makes | Buys | Emits |
|---|---|---|---|
| electricity production | 1 kWh electricity | | 0.5 kg CO₂, 0.001 kg methane |
| fabric production | 1 kg cotton fabric | 3 kWh electricity | 2.0 kg CO₂, 0.004 kg methane |
| incineration of textile waste | treats 1 kg textile waste | | 1.2 kg CO₂ |

The method counts 1 kg of CO₂ as 1 kg CO₂-eq and 1 kg of methane as 29.8. The question: what is the footprint of one T-shirt?

## 1. The background is imported once, with every score solved

Every product of the background has a score: the emissions of one unit of it, with everything upstream.

- Electricity: 0.5 + 0.001 × 29.8 = 0.5298 kg CO₂-eq per kWh.
- Fabric: its own 2.0 + 0.004 × 29.8 = 2.1192, plus the 3 kWh it buys at 0.5298: 3.7086 per kg.

In a real background the suppliers form loops. Electricity needs steel, and steel needs electricity. So the scores are the solution of a system of linear equations, one equation per product. headwater solves that system when you load a background, once, for every product at once, and checks how accurate the answer is. On ecoinvent 3.12 that is about 26,500 scores per indicator.

A background is imported once per version into a **release**: a folder of Parquet files named by the hash of its content, such as `toy-background@6864b7dbb65d`. A release never changes. Your databases name the release they were written for, so a number can always be calculated again on the same data. headwater finds a release by that name in your release cache, the folder `HEADWATER_HOME` names, `~/.headwater` by default. For ecoinvent, one person runs `headwater release import` once, and everyone else pulls the release into their cache, once per machine, and loads it by name:

```python
import headwater as hw

bg = hw.load("toy-background@6864b7dbb65d")
bg.score("toy:fabric-production")  # 3.7086
```

Brightway solves one product at a time. headwater solves the transposed system, which gives every product's score for one indicator in one solve ([calculation.md](calculation.md#three-vectors)).

## 2. Your databases are two CSV tables in git

The processes you model yourself, Brightway's foreground, form a **database**: a folder of text files. The T-shirt's database:

```
tshirt/
  database.yaml    name: tshirt, the release it pins, its methods
  processes.csv    one row per process: id, name, location, kind
  exchanges.csv    one row per line of a process
```

`exchanges.csv` holds what each process makes, buys and emits. Each row is a **line**:

```
process,line,role,flow,amount,unit,supplier
tshirt,e01,reference,tshirt:tshirt,1,unit,
tshirt,e02,input,toy:cotton-fabric,0.25,kg,toy:fabric-production
tshirt,e03,input,toy:electricity,0.5,kWh,toy:electricity-production
```

The **role** says what a line means: `reference` is what the process makes, `input` what it buys, from the **supplier** named by its id. Amounts are always positive, and the role decides the sign in the matrix. Ids are text that never changes, such as `toy:fabric-production`, never a row number.

Because a database is text in git, a change is a one-line diff. Make the fabric 0.30 kg, and the pull request shows that one row. `headwater diff` scores the database before and after and names the line that caused the change, and CI posts the same report as a comment on the pull request.

A few optional files cover the rest: an override of a background process for this database only, named parameters and formulas, cut-offs, flows the method does not count, and `uses:` to buy from another database ([design.md](design.md#databases)).

## 3. A calculation reuses the stored scores, so it takes milliseconds

The T-shirt buys 0.25 kg of fabric and 0.5 kWh. Each already has a stored score, so:

```
T-shirt = 0.25 × 3.7086 + 0.5 × 0.5298 = 0.92715 + 0.26490 = 1.19205 kg CO₂-eq
```

No solve of the background, only a multiplication. Change the fabric to 0.30 kg and the answer is 1.37748, again a multiplication. When your own processes buy from each other, a backpack from a sewing step say, headwater works through them suppliers first, and solves a small system only if they loop.

The same stored scores explain the number:

- **Contributions**, what it is made of: 0.25 kg × 3.7086 = 0.92715 of fabric, 77.8%.
- **Where**, which processes emit: electricity runs 1.25 kWh for one T-shirt, 0.5 bought directly and 0.75 through the fabric, so it emits 0.66225, 55.6%.
- **Sensitivity**, which lines matter: one more kWh per kg of fabric adds 0.5298 × 0.25 = 0.13245.

A scenario that changes the background itself, a cleaner grid say, needs new scores. headwater updates the stored ones with a few extra solves, or factorizes again when many processes change ([calculation.md](calculation.md#edits-and-scenarios)). The background on disk never changes.

On a synthetic background the size of ecoinvent, a study scores in 42 ms once the background is loaded, and loading it takes about 7 s ([benchmarks](appendix/benchmarks.md#study-queries-on-a-release)).

## Studies ask the question

A database says what its products are made of. A **study** asks a question of databases: which products, how much of each, with which methods, and in which **scenarios**. It is a folder next to the databases, with a `study.yaml`, and it holds no processes. The demo's study `tshirt-footprint` asks for one dyed T-shirt, 1.242646 kg CO₂-eq, and its scenario `cleaner-dye` lowers the dye's CO₂ from 4 to 3.5 kg per kg: 1.237646.

```bash
headwater calc project/tshirt-footprint
headwater calc project/tshirt-footprint --scenario cleaner-dye
```

## Numbers you can trust

- **An error, never a guess.** A supplier that does not exist, a unit headwater does not know, a flow of your own that the method does not count, a matrix that cannot be solved: each stops the calculation with a message that names the line or the process. A typo such as `toy:fabric-productoin` is refused, with the closest id suggested.
- **Provenance.** Every result records the release and its hash, the method, a hash of every input, the solver settings and the residual, the accuracy check of the solve.
- **Hand-checked tests.** The numbers on this page are tests: 1.19205, 1.25205 with 0.05 kg of scraps incinerated, 0.13245, 1.242646 and 1.237646. A change that moves one fails CI.

## Read more

- [calculation.md](calculation.md): the math, the algorithms, uncertainty and the Python API.
- [design.md](design.md): the data model, ids, roles and signs, and every file format.
- [workflows.md](workflows.md): setup with ecoinvent, every command, git and CI.
- [Glossary](appendix/glossary.md): every term, with an example.
- The notebooks: [01-first-footprint](../notebooks/01-first-footprint.ipynb), [02-writing-a-database](../notebooks/02-writing-a-database.ipynb) and [03-case-study](../notebooks/03-case-study.ipynb).
