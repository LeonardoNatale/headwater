# Calculation

How headwater computes, explains and updates results. The short version: factorize the background matrix once, store one score vector per indicator, and answer every other question from those two things.

Times for every operation below are in [appendix/benchmarks.md](appendix/benchmarks.md#calculation-core).

## The toy background

This is the first test fixture. All numbers on this page were checked by hand.

| Process | Makes | Uses | Emits |
|---|---|---|---|
| electricity production | 1 kWh | | 0.5 kg CO₂, 0.001 kg CH₄ |
| fabric production | 1 kg cotton fabric | 3 kWh electricity | 2.0 kg CO₂, 0.004 kg CH₄ |
| incineration of textile waste | treats 1 kg | | 1.2 kg CO₂ |

Method `toy-climate`: CO₂ = 1, CH₄ = 29.8 kg CO₂-eq per kg.

The database `tshirt`, one process: one T-shirt uses 0.25 kg of fabric and 0.5 kWh of electricity, and (in the second version) sends 0.05 kg of scraps to incineration.

## Three vectors

Everything the engine does uses three vectors. For the T-shirt without the waste line:

| | s: how much each process runs, for 1 T-shirt | x: score per unit of each product | h: direct emissions per unit, characterised |
|---|---|---|---|
| T-shirt | 1 | 1.19205 | 0 |
| fabric | 0.25 kg | 3.7086 | 2.1192 |
| electricity | 1.25 kWh | 0.5298 | 0.5298 |

**Two roads to the same number.**

```
forward:   h · s = 0×1 + 2.1192×0.25 + 0.5298×1.25 = 1.19205
backward:  x[T-shirt]                                = 1.19205
```

The forward road solves for `s`, one product at a time, and tells you **where** emissions physically happen. The backward road solves the transposed system for `x`, one indicator at a time, and gives **every** product's score at once.

**Contributions are x again.** Every score is direct emissions plus what is bought times the stored score of what is bought:

```
T-shirt 1.19205 = 0.25 kg fabric × 3.7086  = 0.92715  (77.8%)
                + 0.5 kWh       × 0.5298  = 0.26490  (22.2%)
fabric  3.7086  = direct                   = 2.1192
                + 3 kWh         × 0.5298  = 1.5894
```

This is exact at every level, so a "why" tree can be opened as deep as needed without any new solve.

**Sensitivity is x times s.** The change in the T-shirt's score per extra kWh of electricity per kg of fabric is the score of what is bought times how much the buyer runs:

```
x[electricity] × s[fabric] = 0.5298 × 0.25 = 0.13245
check: 3 → 4 kWh per kg fabric moves the T-shirt from 1.19205 to 1.32450
```

**With the waste line.** Incineration has a burden of 1.2 per kg treated, so 0.05 kg sent adds 0.06: the T-shirt scores **1.25205**. Inside the matrix this is stored the ecoinvent way (−0.05 kg × −1.2), which gives the same +0.06.

## The general case

- `A` (technosphere): one column per process, one row per product. The reference amount is on the diagonal; inputs are negative.
- `B` (biosphere): one column per process, one row per elementary flow.
- `c`: characterisation factors of one indicator, one per elementary flow.
- `h = Bᵀ c`: direct characterised emissions of each process.
- `f`: what you ask for (1 T-shirt).

```
s = A⁻¹ f          forward: how much each process runs
x = A⁻ᵀ h          backward: score per unit of every product, for one indicator
score = h · s = x · f
d score / d A[i,j] = −x[i] × s[j]
d score / d B[k,j] = c[k] × s[j]
```

## Single scores

A normalisation and weighting set turns the scores of several indicators into one number, as SimaPro and openLCA do. Each score is divided by its normalisation factor, for EF 3.1 one person's yearly impact, then multiplied by its weight, and the weighted scores add up to the single score.

The toy method `toy-impacts` has climate change, as `toy-climate` has it, its fossil part, and ozone formation, with methane at 1 kg NMVOC-eq per kg. Both toy gases are fossil, so the fossil part equals the total. The set `toy-nw` is made up for the toy:

| Indicator | T-shirt | Normalisation | Normalised | Weight | Weighted |
|---|---|---|---|---|---|
| climate change | 1.19205 kg CO₂-eq | 10 | 0.119205 | 0.6 | 0.071523 Pt |
| ozone formation | 0.00225 kg NMVOC-eq | 0.01 | 0.225 | 0.4 | 0.09 Pt |
| **single score** | | | | | **0.161523 Pt** |

The fossil part has no row, so it counts in none of the set's scores, as EF 3.1's climate sub-indicators count in its total only. Every result of the set lists it as left out.

**A single score is one more indicator.** A score is linear in `h`, so a weighted sum of indicators is an indicator whose factors, `h` and `x` are the same weighted sum of theirs. With `k` = weight / normalisation, 0.06 for climate change and 40 for ozone formation:

```
h_single = 0.06 h_climate + 40 h_ozone
x_single = 0.06 x_climate + 40 x_ozone
electricity  0.06 × 0.5298 + 40 × 0.001     = 0.071788
fabric       0.06 × 3.7086 + 40 × 0.007     = 0.502516
T-shirt      0.25 × 0.502516 + 0.5 × 0.071788 = 0.161523
```

So it needs no new solve, and `contributions`, `where`, `sensitivity`, scenarios and `diff` work on it as on any indicator. Its tree adds up at every level. Each process's part in `where`, and each line's sensitivity, is the same weighted sum of those of each indicator. Elasticities are not, since each is relative to its own score.

- The sum is checked row by row against Aᵀ x = h, as a solve is ([Factorize](#factorize)): scores of opposite sign can cancel. A row that fails is solved again from the sum.
- A product is an exact zero when it is one for every indicator of the sum. Incineration emits no methane, so its normalised ozone formation is exactly 0, and its single score is 0.06 × 1.2 = 0.072.
- A normalised score has no unit. A weighted score and the single score have the set's unit, Pt. A set with no weights gives normalised scores only, and a set with no normalisation weighs the scores as they are, as SimaPro allows.
- Nothing is stored: the set's scores are worked out from the stored vectors when the background is loaded, and an indicators release holds the method's own scores only.

Brightway, checked in bw2calc and bw2data 4, has a `Normalization` of one factor per biosphere flow and a `Weighting` of one number per method, and `MultiLCA` computes each weighted category on its own. It has no score summed across indicators: a Brightway user adds the 16 weighted EF 3.1 scores by hand.

## What is stored

1. **A factorization of A**, one per background release. Built in seconds, kept in the worker's memory. Any solve, forward or transposed, then takes milliseconds. Small scenarios reuse it through low-rank updates; only large scenarios get their own.
2. **One score vector per indicator**, saved as Parquet (`indicators/<method>.parquet`) in a derived indicators release that pins its parent, so adding a method never changes the parent's hash. Scoring databases and SKUs needs only these, not the factorization.

## Background and database

The background never uses a database's processes, so the two are solved separately:

1. The background is factorized and its indicator vectors computed once per release.
2. A database's processes are evaluated with suppliers before buyers. Each database process scores its direct emissions plus the sum of amount × score over what it buys, from the background's stored vectors or from other database processes already computed.
3. If database processes form a loop among themselves (rare: internal recycling), that loop is solved as a small sparse system.

Database edits never touch the background.

## Questions and how they are answered

The engine takes questions, not instructions. A small planner picks the cheapest way to answer from what is stored.

| Question | How |
|---|---|
| Score of a background product | Look up `x` |
| Which process is this? | Match the text against the names and products held in memory, by six ordered rules. No index file. |
| Scores of every background product, one indicator | One transposed solve with the stored factorization |
| Score of a database | Database processes in supplier-first order, using stored `x` |
| Normalised, weighted and single scores of a set | The same weighted sum of the stored vectors, no solve ([Single scores](#single-scores)) |
| Thousands of SKUs | Sparse multiply: SKU × product amounts, times the matrix of stored vectors. Or a DuckDB join and group-by. |
| Why is this score what it is? | Walk the tree: direct + Σ amount × score of supplier, recursively, pruned below a share of the total |
| Where do the emissions physically happen? | One forward solve for `s`, then `h × s` per process, or `B × s` per elementary flow |
| Which inputs matter most? | `x[i] × s[j]` for every matrix entry, after one forward solve |
| Which named parameters matter most? | Chain rule: entry sensitivities × derivative of each formula with respect to the parameter |
| What if one process changes? | Rank-1 update of all scores with the stored factorization |
| What if the whole background changes? | New factorization |

## Algorithms

### Assemble

Matrices are built in DuckDB, in memory, from the release's Parquet files: SQL assigns row and column numbers to ids, applies the role signs, and returns arrays straight into NumPy. Row numbers never leave the calculation.

### Factorize

1. Scale rows and columns so the largest entry of each is 1.
2. Factorize with SuperLU, a fill-reducing column ordering (minimum degree on Aᵀ+A), and no pivoting. SuperLU still swaps rows when a diagonal pivot is exactly zero, so whether pivoting happened is recorded from the row and column permutations, not from the setting.
3. Check the factors on one solve: the scores for an emission of 1 per run of every process. If that solve fails the check below after one step of iterative refinement, factorize again with pivoting, and keep those factors.
4. Estimate the condition, from the factors kept. Above 10¹⁰ the matrix counts as nearly singular: stop with an error naming the processes involved.
5. In every solve, set the rows that are zero by structure to exactly 0 (see exact zeros below).
6. Check every result row by row with its componentwise backward error, `|b − M y| / (|M| |y| + |b|)`, so small scores are checked against their own size, not against the largest score. Scores in ecoinvent span many orders of magnitude, so a global check hides errors on small ones. The tolerance is 10⁻¹².
7. If a row fails, do one step of iterative refinement. If it still fails, factorize again with pivoting and hand those factors back, so a background solves with them from then on. If that fails, stop with an error naming the processes involved, for example a process with no reference output.

**The condition** says how much a score can move when the data moves. Example: give every toy process an emission of 1 per run, then move every entry of A and every emission by at most 0.1%. No score moves by more than about 0.38%: the condition is 3.8. Formally, take the scores `x` of an emission of 1 per run of every process, and `g = |Aᵀ| |x| + 1`, the sum of the absolute terms of each score's equation. The condition is the largest `(|A⁻ᵀ| g)_j / (g_j / 2|A_jj|)`. When no term cancels another, `g_j / 2|A_jj|` is the score itself and this is the usual componentwise condition; when a credit cancels a burden, the score can be 0 but its terms are still a fair size. It does not change with the unit of a product. It is estimated from about ten solves with the stored factors (Hager's method, as LAPACK does it), and computed exactly below 100 processes.

| Matrix | Condition |
|---|---|
| The toy background (electricity, fabric, incineration) | 3.5 |
| The toy T-shirt database with its background | 3.8 |
| ecoinvent 3.12 cut-off | 30 |
| The toy with a loop between fabric and electricity whose gain is 1 − 10⁻⁴, 1 − 10⁻⁸, 1 − 10⁻¹² | 4 × 10⁴, 4 × 10⁸, 4 × 10¹² |
| The same loop with a gain of 1 + 2 × 10⁻¹⁶ (singular up to rounding) | 2 × 10¹⁶ |

The usual normwise condition number is no use here: it is 3.4 × 10¹⁴ for ecoinvent after scaling and 10²⁴ before, because infrastructure is counted per unit. Yet rounding every entry of A to float32, a relative change of up to 6 × 10⁻⁸, moves no ecoinvent score by more than 1.3 × 10⁻⁷. The limit of 10¹⁰ is 8 orders above ecoinvent; at the limit, rounding errors of 10⁻¹⁶ in the data can move a score by 10⁻⁶. The ecoinvent numbers here come from [bench/parity_numerics.py](../bench/parity_numerics.py), listed in the [benchmarks](appendix/benchmarks.md#numerics-on-ecoinvent).

**The tolerance.** On ecoinvent 3.12 cut-off the largest backward error is 4 × 10⁻¹⁵, without refinement. With a condition of 30, a score that passes 10⁻¹² is within about 3 × 10⁻¹¹ of the exact score of its matrix, when its positive and negative parts do not cancel. A score that is small because they cancel can be less accurate than that, relative to its own size.

**Exact zeros.** A product with no emission anywhere upstream has a score of exactly 0. Example: the toy's incineration when it emits nothing. ecoinvent 3.12 cut-off has 2,041 such products for the 3-flow climate method of the [benchmarks](appendix/benchmarks.md#parity-with-brightway), among them the recycled-content datasets. A solve can leave rounding noise there (pivoted factors leave about 10⁻⁹), and a relative check of noise against noise fails whatever its size. These rows are found from the sparsity of A alone, by following purchases upstream from the processes that emit, and set to 0 on every solve. They are found per indicator, from the processes with a non-zero `h` for it: a product can be an exact zero for climate change and not for water use. A result's provenance says how many there are for its indicator, and an indicators release says it for each indicator, so nobody takes them for computed values.

**Scores that cancel.** A score can be near 0 because its positive and negative parts cancel. Example: 1 kg of fabric takes 1.5 kg of CO₂ from the air, which counts −1.5 for biogenic carbon, and its residue gives 1.5 kg back, which counts +1.5. The fabric's biogenic score is 0, and a reader who sees 0 should know it is +1.5 − 1.5. With 0.4 kg given back it is −1.1, made of +0.4 and −1.5, and does not cancel.

- A score's terms are `c[f] × B[f, p] × s[p]`, one per flow and process that runs. The positive ones add up to its positive part, the negative ones to its negative part. Their sizes add up to its gross: 3.0 for +1.5 − 1.5.
- A score cancels when its gross is not 0 and the score is less than 10⁻³ of its gross. Below that the score also loses accuracy relative to its own size (the tolerance above): an error of 10⁻¹² of the gross is 10⁻⁹ of the score.
- Terms are per flow, not per process. A process that takes CO₂ from the air and emits it again still has two parts.
- An exact zero has no parts and does not cancel.
- `result.provenance.cancellation` holds the positive part, the negative part and `cancels`. `print(result)` ends with `cancels: +1.5 and -1.5`, and `headwater calc` lists the indicators whose score cancels. The function is `calc.cancellation`, on arrays.

With the 3-flow climate method no ecoinvent 3.12 score is below 10⁻³ of its parts ([benchmarks](appendix/benchmarks.md#numerics-on-ecoinvent)). Biogenic carbon is where scores cancel: the products of 3.12 it flags, per indicator, are in the [benchmarks](appendix/benchmarks.md#carbon-indicators-on-ecoinvent-312).

One case the checks cannot catch, handled outside the solver: **a loop that consumes more than it makes** (an electricity process that needs more electricity than it delivers, through its suppliers). The matrix is not singular, the solve succeeds, and scores come out negative. This is a modelling error, caught by a separate physical-sense check on the database, not by factorization.

The no-pivoting choice is tested on cut-off and APOS data, both ecoinvent 3.12 ([benchmarks](appendix/benchmarks.md#apos-against-brightway-and-ecoinvent)). Consequential data has negative substitution entries, which is where it is most likely to fail; test it before relying on it there.

### Explain

Recursive, from stored scores, no solve. Each node lists direct emissions and each purchase as amount × score of supplier. Children are sorted by size, pruned below a share of the root total (for example 3%), and limited to a maximum depth. The supply graph has loops, so pruning and the depth limit are what stop the recursion.

Three views of one result, all cheap:

- **What it buys** (the tree above).
- **Where it is emitted**: emitting processes anywhere upstream, from one forward solve. Each process's part is its direct emissions times how much it runs, and the parts are also summed by location.
- **Sensitivity analysis**: what a user can change (inputs, wastes, emissions, resources, own use, avoided products), ranked by elasticity, that is the % change in score per 1% change of that line. A process's own reference line is left out unless asked for.

### Edits and scenarios

- **Editing one process is a rank-1 change.** Any number of edits inside one process changes one column of A, so all scores update with one extra transposed solve (Sherman-Morrison). Edits to k processes are rank k (Woodbury).
- **Editing only a process's direct emissions** also costs one transposed solve, with no correction term.
- **Refactorize instead** when k gets large. Example: with a crossover of 60, a scenario that changes 60 processes is updated and one that changes 61 is solved by factorizing the edited A again. The switch is `CROSSOVER` changed columns of A, in `calc/update.py`: **120**, measured on ecoinvent 3.10 cut-off (23,522 processes); 3.12 has 26,533 and is not yet measured. It is the largest count where the update worked and was faster in every measured case. Beyond it the update starts to refuse edits as singular, long before it gets slower: it costs about 5 ms per changed column against 2.8 s for a new factorization ([benchmarks](appendix/benchmarks.md#scenario-crossover), script [bench/scenario_crossover.py](../bench/scenario_crossover.py), a busy machine).
- **No dependency tracking.** In ecoinvent 3.12, 16,962 of 26,533 processes (64%) sit in one loop where every process supplies every other ([benchmarks](appendix/benchmarks.md#calculation-core)). A change to any of them moves most scores, so "recompute only what depends on it" saves almost nothing. Low-rank updates do.

**A named scenario is such an edit.** Example: `clean-grid` says the grid emits 0.3 kg CO₂ per kWh instead of 0.5, for everything. Only electricity's column of B changes, so one transposed solve from the stored scores gives every new score: electricity 0.3298, fabric 2.1192 + 3 × 0.3298 = 3.1086, and the T-shirt 0.25 × 3.1086 + 0.5 × 0.3298 = 0.94205. A scenario where fabric takes 2.5 kWh per kg changes a column of A: the update adds one transposed solve per changed process (Woodbury), and how much each process runs takes two forward solves instead of one. The update is checked as a factorization is, with solves of the update: the residual of every row, and a condition estimate of the edited matrix, refused above the limit. Up to the crossover it updates. Above it, or when the update fails its checks (its residual check, or its test that the edited matrix is singular), it factorizes the edited A again and solves with those factors. A matrix that really is singular still stops there, with the processes involved. Only changed columns of A count: a scenario that changes emissions alone needs no update of A. The result's `provenance.update_route` says which way it went and why in one line, for example `refactorized: 140 changed processes, above the crossover of 60`, and its path holds `refactorized` when A was factorized again. The background is never changed: the edited matrix and scores belong to the result.

**A local override is not an edit.** The database gets its own copy of the process, in the small block of its own processes. Only the database's purchases from that process use the copy, and the copy buys from the background as it is. So the background, its factorization and its scores stay as they are.

### Many products at once

A portfolio of SKUs is a sparse matrix of amounts (SKU × background product). Scores for every indicator are one sparse multiply with the stored vectors. The same works as a DuckDB join and group-by when the SKU data already lives in tables.

## Numbers you can trust

- float64 everywhere, in files and in memory.
- Every result records release hashes, method and version, input hash, engine version, solver settings and its residual.
- A database's own flow with no factor is an error of `validate` and `calc` alike, until a person acknowledges it in `no_factor.csv` with a reason. Then it counts 0 and the result lists it with that reason. Background flows the method does not cover are listed on the result.
- Results compare across runs with a tolerance, never for exact equality.

## Uncertainty

A Monte Carlo run gives a range instead of one number. It draws every uncertain amount of the background, and of the databases it scores, many times and scores each product in each draw.

**The toy.** Say fabric production's 3 kWh of electricity per kg is lognormal, with a median of 3 and a geometric standard deviation (GSD) of 1.1: `loc` ln 3, `scale` ln 1.1. Call the drawn amount E. The T-shirt buys 0.25 kg of fabric and 0.5 kWh, so in every draw

```
T-shirt = 0.25 × (2.1192 + 0.5298 E) + 0.5 × 0.5298 = 0.79470 + 0.13245 E
```

E has percentiles 3 × 1.1^z, with z the standard normal quantile: z = ±1.959964 gives 1.1^z = 1.205392 and 1 / 1.205392. So the T-shirt's range is known exactly:

| | E (kWh) | T-shirt (kg CO₂-eq) |
|---|---|---|
| 2.5% | 2.48882 | 1.12434 |
| median | 3 | 1.19205 |
| mean | 3 × exp(ln²1.1 / 2) = 3.01366 | 1.19386 |
| 97.5% | 3.61618 | 1.27366 |

2,000 iterations of seed 0 give a median of 1.19097 and 1.12249 and 1.27270 for 2.5% and 97.5%, within the Monte Carlo error of these numbers.

**A database's own line.** Now the background is fixed, and the T-shirt's own 0.25 kg of fabric is lognormal with a GSD of 1.1, written next to its amount ([design.md](design.md#uncertainty)):

```
line,role,flow,amount,unit,supplier,distribution,scale,minimum,maximum
e02,input,toy:cotton-fabric,0.25,kg,toy:fabric-production,lognormal,0.0953102,,
```

The amount is the median, so F, the fabric drawn, has percentiles 0.25 × 1.1^z, and in every draw

```
T-shirt = 3.7086 F + 0.5 × 0.5298 = 0.2649 + 3.7086 F
```

| | F (kg) | T-shirt (kg CO₂-eq) |
|---|---|---|
| 2.5% | 0.207401 | 1.034069 |
| median | 0.25 | 1.19205 |
| mean | 0.25 × exp(ln²1.1 / 2) = 0.251138 | 1.196271 |
| 97.5% | 0.301348 | 1.382479 |

2,000 iterations of seed 0 give a median of 1.19483 and 1.04122 and 1.38074 for 2.5% and 97.5%, within the Monte Carlo error of these numbers.

**One iteration.**

1. Draw every line that has a distribution. Each line gets one uniform u in (0, 1) and its amount is the inverse of its distribution's CDF at u: exp(loc + scale × z) for a lognormal, loc + scale × z for a normal, with z the normal quantile of u, and the same for a uniform and a triangular. A lognormal on a negative amount stays negative: `loc` is the mean of ln|x|. A distribution is in the unit of its line, and is taken to the flow's reference unit with the amount: 10.8 MJ with `loc` ln 10.8 is 3 kWh with `loc` ln 3.
2. Rebuild A and B, factorize A again with every check of [Factorize](#factorize), and solve the scores of every product for each indicator, one transposed solve each. One iteration serves every product and every database of the run.
3. Score each item. A background product reads its score. A product of a database is direct + amounts · x, where `amounts` is what one unit of it buys from each background process through all its own processes, and `direct` its own emissions. For the T-shirt, `amounts` is 0.25 kg of fabric and 0.5 kWh, and `direct` is 0. When none of its own lines or parameters is drawn, `amounts` and `direct` are the same in every iteration. When some are, its own processes are built again with the drawn amounts, factorized with the same checks, and solved forward for one unit of its reference flow, which gives that iteration's `amounts` and `direct`.

**The same iteration, the same draw.** The uniforms of iteration k come from a generator seeded by (seed, k), numpy's PCG64 with `SeedSequence(seed, spawn_key=(k,))`, one per uncertain line in the order of (process id, line id). So a line's draw depends on the seed, k and the release, never on matrix indices or on what is asked. Every item of a run uses the same iterations, and two runs with the same seed draw the same amounts. Products and databases that share suppliers stay correlated: with the grid's 0.5 kg of CO₂ per kWh drawn, the T-shirt minus the T-shirt that also sends 0.05 kg of scraps to incineration is -0.06 in every iteration, a standard deviation of 0. Drawn with different seeds, the same difference has a standard deviation of 0.16 over 2,000 iterations. Without the shared iterations, a comparison looks far more uncertain than it is.

**A database's own draws are keyed by their ids.** A line of a database is drawn from a uniform of its own, from `SeedSequence(seed, spawn_key=(k, w))`, where w is the sha256 of its key as 8 words: its process id and line id, `tshirt:tshirt` line e02. A parameter's key is its database's name and its id, `tshirt` and `fabric_kg`. The background's uniforms come from `spawn_key=(k,)`, so a database's draws never move them. A process id holds its database's name, so a line draws the same in any run, alone or beside other items:

- The dye's line, in the database `dyes`, draws the same when `dyes` is asked alone and when the dyed T-shirt that uses it is asked: the dyed T-shirt is the T-shirt plus 0.01 times the dye in every iteration.
- Two versions of a database draw the same line the same way. The toy cases `tshirt` and `tshirt-waste` both hold `tshirt:tshirt` line e02: drawn with the same seed, one run each, the second is the first plus 0.06 in every iteration.

**What is drawn, and what is fixed.**

- Drawn: every line of the background with a distribution, in A or B: lognormal, normal, uniform or triangular, with the parameters of the release's `uncertainty` column ([design.md](design.md#a-release)). ecoinvent 3.12 cut-off has 270,167 such lines in A and 347,143 in B.
- Drawn: a database's own line with a distribution, and a parameter with one ([design.md](design.md#uncertainty)), from their keys.
- Fixed: a line with no distribution, the characterisation factors, and a line that an override or a scenario sets (below).
- Each line on its own, as Brightway does. So a market's shares sum to 1 only at their medians. Example: a market that buys 0.6 and 0.4 kWh, each lognormal with a GSD of 1.1. In a draw the two shares sum to exp(ln²1.1 / 2) = 1.00455 on average, with a standard deviation of 1.00455 × 0.09553 × √(0.6² + 0.4²) = 0.0692: the market's score moves by about 7% while every supplier stays fixed.
- A normal, uniform or triangular amount can be drawn below 0, an input of 0.1 ± 0.1 at -0.02. It is kept, as Brightway keeps it, and counted: `provenance.opposite_signs`. On ecoinvent 3.12 there are about 2,190 such draws per iteration, from the 5,949 normal lines: 2,150 of them emissions whose standard deviation is close to their mean, about 20 inputs.

**Local overrides.** A database's copy of a background process keeps most of its lines, and each line it keeps is drawn as the background draws it: the same uniform, so the copy and the original move together. Example: with fabric's E kWh drawn, the mill's fabric emits 1.5 kg of CO₂ instead of 2.0. The T-shirt that buys the mill's copy is 0.25 × 0.5 = 0.125 below the T-shirt in every iteration, a standard deviation of 0.

- A line the override sets is fixed at the override's value. With the mill at 2.5 kWh per kg, the T-shirt is 0.25 × (2.1192 + 2.5 × 0.5298) + 0.5 × 0.5298 = 1.125825 in every iteration.
- Unless the row gives it a distribution: then it is drawn from the copy's own key, `tshirt:copy:toy:fabric-production` line e02.
- A row with the original's amount and unit changes only the supplier: the line keeps the original's distribution and its draw. The mill buying the same 3 kWh from wind at 0.01 kg CO₂ per kWh is 0.25 × (2.1192 + 0.01 E) + 0.2649.

**Scenarios.** A product of a study in a scenario is drawn on the background of that iteration with the scenario's lines in place, so the base and the scenario stay paired. A line the scenario sets, by its amount or its unit, is fixed at the scenario's value; a line whose supplier alone changes keeps its draw. A parameter the scenario sets is fixed. Example: with fabric's E drawn, the T-shirt is 0.79470 + 0.13245 E, and in `clean-grid`, where the grid emits 0.3 kg CO₂ per kWh, 0.6947 + 0.08245 E. Their difference, 0.1 + 0.05 E, has a standard deviation of 0.05 × 0.28789 = 0.0144. Drawn with different seeds it would be √(0.13245² + 0.08245²) × 0.28789 = 0.0449. A scenario that sets fabric's E at 2.5 kWh makes the T-shirt 1.125825 in every iteration.

A scenario that changes only emissions, as `clean-grid` does, uses the iteration's factors and one more transposed solve per indicator. One that changes purchases updates the iteration's factors, as `calc` does ([Edits and scenarios](#edits-and-scenarios)): a low-rank update up to the crossover, a new factorization above it or when the update fails its check.

**Parameters.** A parameter with a distribution is drawn once per iteration, and every formula is worked out again with it, so every line that uses it moves together. Example: the T-shirt's fabric is `fabric_kg`, lognormal with median 0.25 and a GSD of 1.1, and its scraps sent to incineration are `fabric_kg * 0.2`. With `fabric_kg` drawn as P, the T-shirt is 0.2649 + 3.7086 P + 1.2 × 0.2 P in every iteration. A parameter that is a formula follows those it uses: `fabric_kg = net_kg * (1 + loss)` with `net_kg` drawn moves the fabric by 1.05 times `net_kg`. A used database's parameters are drawn with its own name, so they too draw the same alone or inside the database that uses it. Brightway stores a distribution on a parameter; drawing it and the formulas that use it is headwater's own.

**Fail loudly.** No iteration is ever dropped. The run stops at the first iteration that cannot give a trustworthy number, with an error that names the iteration, the seed and the processes:

- a reference amount drawn at 0 or below, which would flip or break the scaling of its process
- a net output of 0 or of the opposite sign on the diagonal, from an own use drawn above its reference
- a matrix that is singular, or nearly so: a condition estimate above 10¹⁰
- a residual above the tolerance after refinement and pivoting
- a formula that cannot be worked out with the drawn parameters, such as the logarithm of a drawn value below 0

Each holds for the background, a scenario's background and a database's own processes: a T-shirt whose reference amount is drawn at 0 stops naming `tshirt:tshirt`.

Draws can push the condition up. On ecoinvent 3.12 with 200 iterations its median is 32, 9 iterations are above 10³, one is above 10⁵ (4.3 × 10⁵) and none above 10⁷, 4 orders below the limit ([benchmarks](appendix/benchmarks.md#monte-carlo-on-ecoinvent-312)). An own use drawn above its reference would flip its process's net output: the run stops there too, naming the process; none did in 1,000 draws of 3.12. A loop that a draw makes consume more than it delivers solves, and gives negative scores, as outside Monte Carlo: the run cannot tell it from a credit.

**Where the samples live.** In memory, in the result of one call: one float64 per item, indicator and iteration. 10 products, the 25 indicators of EF 3.1 and 1,000 iterations are 2 MB. The scores of every product in every iteration are not kept: on ecoinvent 3.12 with EF 3.1 they are 26,533 × 25 × 8 bytes, 5.3 MB per iteration and 5.3 GB for 1,000. A run is reproduced instead, from the release, the method, the seed, the number of iterations and the versions of numpy and scipy, which its provenance records.

**What it costs.** One iteration of ecoinvent 3.12 with EF 3.1 takes about 3.6 s, of which 3.3 s is the factorization ([benchmarks](appendix/benchmarks.md#monte-carlo-on-ecoinvent-312)). 200 iterations take about 12 minutes and 1,000 about an hour. `workers` runs blocks of iterations in that many processes, with the same samples. A database whose own lines are drawn adds its own small solve: the 3.12 pilot (40 processes, 719 lines drawn), with an override and in a scenario that changes a purchase, took 3.90 s per iteration against 3.71 s for the background alone ([benchmarks](appendix/benchmarks.md#monte-carlo-of-a-database-on-ecoinvent-312)).

**Against Brightway.** On 10 products of ecoinvent 3.12 and the 25 indicators of EF 3.1, 200 iterations of each, the mean and the 2.5 and 97.5 percentiles agree within Monte Carlo error: 746 of 750 within 3 standard errors, the 4 others tail percentiles at 3.02 to 3.23 ([benchmarks](appendix/benchmarks.md#monte-carlo-on-ecoinvent-312)).

### Python API

**`hw.monte_carlo(bg, items, *, iterations, seed=0, indicators=None, percentiles=(2.5, 97.5), workers=1)`** draws `iterations` times and returns a `MonteCarlo`. `items` are background products, by the id of the process that makes them, products of databases, `(database, "tshirt:tshirt")` with the database opened by `hw.open`, products of studies, `(study, "tshirt:tshirt")`, its amount of it, and products of studies in one of their scenarios, `(study, "tshirt:tshirt", "clean-grid")`. A tuple is read as a list of items, so a product goes inside the list. `indicators` are every indicator the background scores by default, the normalised, weighted and single scores of its sets too: a single score is a weighted sum, so it is drawn with the indicators it weighs.

```python
mc = hw.monte_carlo(bg, [(hw.open(f"{toy}/tshirt/tshirt"), "tshirt:tshirt"), "toy:fabric-production"],
                    iterations=2000, seed=0)
print(mc.range("tshirt:tshirt"))
# tshirt:tshirt climate-change: score 1.19205 kg CO2-eq, mean 1.1932, median 1.19097, sd 0.038,
#   2.5% 1.12249, 97.5% 1.2727
```

| Attribute or method | Meaning |
|---|---|
| `items`, `indicators`, `units` | A product by the id of its process, `toy:fabric-production` or `tshirt:tshirt`, and in a scenario with the scenario in brackets, `tshirt:tshirt[clean-grid]`. Two items with the same label, such as the T-shirts of two toy cases, are refused as asked twice: run them in two calls with the same seed, which draw the same |
| `scores` | The score with no draw, one per item and indicator: what `calc` gives, in its scenario |
| `samples` | One float64 per item, indicator and iteration: `samples[i, j, k]` |
| `range(item, indicator=None)` | A `Range`: `score`, `mean`, `median`, `sd` and `percentiles`, by percent |
| `difference(a, b, indicator=None)` | The `Range` of a minus b, iteration by iteration |
| `ranges()`, `to_arrow()`, `print(mc)` | Every item and indicator, as `Range`s, a table or text |
| `conditions` | The condition estimate of each iteration's factors |
| `provenance` | The seed, the iterations, the generator, the numpy and scipy versions, the input hash, the release, the methods, the solver, how many lines of the background were drawn by distribution, how many lines of the databases (`database_lines`) and parameters (`parameters`) were drawn by their own keys, `opposite_signs`, `largest_condition` (scenarios and database blocks included), `residual`, `pivoted`, how many solves of a scenario factorized its A again (`scenario_refactorized`) and how markets are drawn |

It refuses, before any draw: a background read from a folder; a run where nothing is drawn, no line of the release and no line or parameter of a database asked having a distribution; every database or study `calc` refuses; an unknown product, with the closest id; an unknown scenario, with the study's scenarios; an item given twice; fewer than 2 iterations; a scenario that sets a background line by a formula, in a database that draws parameters; a scenario that sets a parameter of a database a product uses. The command is `headwater calc <database> --iterations N`, or `headwater calc <study> --iterations N --scenario NAME` for a study's product in a scenario ([workflows.md](workflows.md#command-line)).

### Later

- **A stored samples release.** The scores of every product in every iteration, frozen as a derived release that pins the release, the method, the seed, the number of iterations and the generator, so a database is scored on 1,000 iterations in milliseconds. 5.3 GB for 1,000 iterations of 3.12 with EF 3.1, and presampled results may count differently under the ecoinvent licence ([decisions.md](decisions.md#4-what-may-be-shared-from-licensed-data), Open 4).
- **Draw a market's supplier shares together** (or renormalise them), so they still add up to 1.
- **A background line set by a formula in a scenario**, worked out again with the drawn parameters in each iteration.
- **The Excel converter** carrying a Brightway workbook's distributions into the distribution columns.
- **Fast screen:** elasticity × uncertainty of each input gives a first-order estimate in milliseconds. It ranks what matters; Monte Carlo gives the number.

## Later: regionalised factors

The backward road still works. Build `h` per process with that process's own location: `h[j] = Σ_k B[k,j] × CF(k, location of j)`, then solve once per indicator as usual.

## Python API

Load a background once, then score databases on it. The toy T-shirt, from the toy folder:

```python
import headwater as hw

toy = "tests/fixtures/toy"
bg = hw.load(f"{toy}/background", method=f"{toy}/methods/toy-climate", namespace="toy")
database = hw.open(f"{toy}/tshirt/tshirt")
result = database.calc(bg, "tshirt:tshirt")

print(result)
# Result(tshirt:tshirt = 1.19205 kg CO2-eq, indicator climate-change, method toy-climate 1)
```

### What you can call

`headwater` exports the names below, and nothing else. `tests/query/test_api.py` lists them, and the public names of `Background`, `Database` and `Result`, so the surface changes only on purpose. Each object prints on one line, as in the examples.

Names follow ISO 14040/44 where it has a term, and Brightway otherwise: `contributions` is a contribution analysis, as bw2analyzer's, `sensitivity` a sensitivity analysis, and `score_many` scores many products at once, as Brightway's `MultiLCA` does.

| Name | What it is | Example on the toy |
|---|---|---|
| `load` | Read a background and solve it once | `bg = hw.load(f"{toy}/background", method=f"{toy}/methods/toy-climate", namespace="toy")` |
| `open` | Read a database folder | `database = hw.open(f"{toy}/tshirt/tshirt")` |
| `score_many` | Score a table of SKUs | `hw.score_many(bg, {"sku": ["a"], "product_id": ["toy:fabric-production"], "amount": [2.0]})` |
| `monte_carlo` | A range per item and indicator | `hw.monte_carlo(bg, [(database, "tshirt:tshirt")], iterations=2000, seed=0)` |
| `Background` | What `load` returns | `Background(folder background, namespace toy, method toy-climate 1, 1 indicator, 3 processes)` |
| `Study` | What `open` returns for a study folder, or a study made in memory | `Study(tshirt-footprint: 1 product, methods toy-climate, scenarios cleaner-dye)` |
| `Database` | What `open` returns for a database folder | `Database(tshirt: 1 process, background toy-background@fixture, methods toy-climate)`, or `Database(dyes: 1 process, background toy-background@fixture, no methods)` |
| `Result` | What `database.calc` returns, one per product for `study.calc` | `Result(tshirt:tshirt = 1.19205 kg CO2-eq, indicator climate-change, method toy-climate 1)` |
| `Provenance` | `result.provenance` | `pin` is `toy-background@fixture`, `releases` is empty for a folder |
| `Contribution` | What `contributions` returns | prints as a tree, below |
| `Where`, `Emitter`, `Place` | What `where` returns, by process and by location | prints as a list, below |
| `Sensitivity` | One line of `sensitivity` | `Sensitivity(tshirt:tshirt e02, T-shirt: input 0.25 kg, elasticity 0.7778, sensitivity 3.7086)` |
| `ParameterSensitivity` | One line of `result.parameters` | `ParameterSensitivity(fabric_kg = 0.25 kg, elasticity 0.7778, sensitivity 3.7086)` |
| `NotCharacterised` | A flow with no factor | `tshirt:cod`, `in_database` true, with the reason of its row |
| `Match` | One line of `bg.find`, or what `bg.get_process` returns | `Match(toy:electricity-production: electricity production \| electricity \| GLO, kWh, name starts with)`, with `flow_id` `toy:electricity` |
| `ManyScores` | What `score_many` returns | `ManyScores(2 SKUs, 1 indicator, method toy-climate 1)` |
| `MonteCarlo`, `Range`, `MonteCarloProvenance` | What `monte_carlo` returns | `MonteCarlo(2 items, 1 indicator, 2000 iterations, seed 0)` |
| `HeadwaterError`, `InvalidDataError` | The errors | `InvalidDataError` lists every problem it found |

A `Background` has these:

| Name | Example on the toy |
|---|---|
| `score(process_id, *, indicator=None)` | `bg.score("toy:fabric-production")` is 3.7086, `bg.score("toy:incineration-textile-waste")` 1.2 |
| `contributions`, `where`, `sensitivity` | [A background product](#a-background-product) |
| `direct_and_upstream(process_id, *, indicator=None)` | `bg.direct_and_upstream("toy:fabric-production")` is (2.1192, 1.5894): [A background product](#a-background-product) |
| `find`, `get_process` | [Finding a process](#finding-a-process) |
| `processes(process_ids)` | `bg.processes(["toy:fabric-production"])` holds its lines e01 to e04, each with the name of its flow, `flow_name`, taken from the flows the background holds. On a release, one call with every id is much faster than one call per id: with 50 ids in a call, about 4.6 ms per id against 42 ms per id alone ([Processes read from a release](appendix/benchmarks.md#processes-read-from-a-release)) |
| `write_indicators` | [Indicator vectors](#indicator-vectors) |
| `method`, `methods`, `sets`, `indicators` | `toy-climate`; one method; no set; one indicator, `climate-change` |
| `release` | None for a folder, else the release, `toy-background@6864b7dbb65d` |
| `not_characterised` | The background's flows that no indicator covers |

A `Database` has `calc`, `validate` and `scores` (below), `name` (`tshirt`) and `folder`, the records read from its files. `calc` and `scores` take the `process_id` of the process whose product they score. `hw.Database.new(path, bg, ...)` and `database.edit(bg)` return a `DatabaseEditor`, which builds and changes a database's files: [Building and editing a database](#building-and-editing-a-database). A `Result` has the attributes and calls of its table below.

The matrices, the row of each process and the checks a database needs are not on `Background`, and the system a result solved is not on `Result`. They hold integer indices, which never leave a calculation ([AGENTS.md](../AGENTS.md), rule 1). The package's own code reaches them with `internals(bg)` and `internals(result)`, from `headwater.query._internals`.

The same T-shirt from a release, a frozen background ([design.md](design.md#a-release)). The release holds its method, so `method` is its id:

```python
bg = hw.load("toy-background@3f9a1c2d4e5f", method="toy-climate")  # from the local cache
database.calc(bg, "tshirt:tshirt").score  # 1.19205, for a database whose pin names this release
```

**`hw.load(path, *, method=None, namespace=None, sets=None)`** checks a background, factorizes it once and solves one score vector per indicator. `path` is one of:

| `path` | Example |
|---|---|
| a release folder, which holds `release.json` | `~/.headwater/releases/toy-background@3f9a1c2d4e5f` |
| a release in the local cache, as `name@hash` with at least 12 characters of the hash | `toy-background@3f9a1c2d4e5f` |
| a folder of processes, `processes.csv` and `exchanges.csv` | `tests/fixtures/toy/background` |

For a release, DuckDB reads the Parquet files and builds A and B, with no Python object per exchange. `method` is the id of a method the release holds, or a method folder (a `Path` is always a folder), and may be left out when the release holds one method. Opening a release checks `release.json` and the sha256 of every file, and `build` already refused unknown ids, suppliers and units. `load` then refuses, naming the line, what `build` does not check: a role that does not fit its flow (an emission on a product), a supplier that makes another flow, a unit that does not convert. Lines resolved as `cut_off` or `data_gap` have no supplier and buy nothing, so they are skipped. An indicators release has no processes to calculate on: `load` names its source release instead.

For a folder, `method` is the method folder, and `namespace` is the prefix of the process ids (`toy:fabric-production`), by default the folder name. It raises `InvalidDataError` for bad links, units or factors of one location, and a calculation error naming the processes if the matrix cannot be solved.

`sets` is a normalisation and weighting set folder, or a list of them ([design.md](design.md#a-normalisation-and-weighting-set)). Each adds its normalised scores, its weighted scores and its single score as indicators, after the method's, worked out from the stored vectors ([Single scores](#single-scores)). Their ids start with the set's: `toy-nw:normalised:climate-change`, `toy-nw:weighted:climate-change`, `toy-nw:single-score`. `bg.indicators` lists them all, the method's first. A set pins its method by hash, and `load` refuses a set whose method is not loaded, or was loaded with other content, naming both hashes:

```python
bg = hw.load(f"{toy}/background", method=f"{toy}/methods/toy-impacts", namespace="toy",
             sets=f"{toy}/methods/toy-nw")
print(bg.contributions("toy:fabric-production", indicator="toy-nw:single-score"))
# 1 kg of cotton fabric production: 0.50252 (100.0%), direct 0.28715
#   3 kWh of electricity production: 0.21536 (42.9%), direct 0.21536
```

`method` can also be a list of several methods used together, ids of methods the release holds or method folders: `method=["methods/EF-3.1", "methods/IPCC-2021"]`. The background then scores the indicators of all of them with one factorization, `bg.methods` holds each, and `bg.method` is them as one, named `EF-3.1+IPCC-2021` in results. An indicator id in two of them is refused, naming both ([design.md](design.md#several-methods-together)).

**`hw.open(path)`** reads a database folder, or a study folder ([A study](#a-study)). **`database.calc(bg, process_id, *, indicator=None)`** scores one unit of what the process `process_id` makes, as Brightway's `LCA({act: 1})`. It checks the database against `bg`, without checking the background's records again, and returns a `Result`. **`database.validate(bg)`** makes the same checks without calculating, and returns a `Report` of errors and warnings, which prints its counts and first problems: `Report(no problems)`, or `Report(3 warnings)` and a line for each. **`database.scores(bg, process_id, *, indicators=None)`** returns `{indicator id: score}` for every indicator at once (or for `indicators`): the numbers `calc` gives, with no scenario, from one assembly of the database and one small factorization, with no solve against the background's matrix. It leaves out the rest of a result (forward solve, `contributions`, `sensitivity`, provenance). `headwater diff` uses it to score each difference made alone to the old database ([workflows.md](workflows.md#command-line)).

**Every process is a product.** A database has no reference flow: each of its processes is a product, scored per unit of what it makes. Example: the T-shirt with a `sewing` process that it buys, 0.1 kWh, gives `database.calc(bg, "tshirt:sewing").score` 0.05298, and 1.24503 for the T-shirt. Two processes that make the same flow are two products, each scored by its own id. The input hash counts the process scored. An id the database does not have raises `ValueError` naming its processes. The processes are `database.folder.processes`, each with its `id`, `name` and `location`, so every product of a database is scored, with the name of its process, such as an activity's name in a workbook, by `[(p.id, p.name, database.scores(bg, p.id)) for p in database.folder.processes]`. `headwater calc` does the same from the command line ([workflows.md](workflows.md#command-line)).

What `calc` does with each part of a database ([design.md](design.md#a-database) has the files):

| Part | Example | What `calc` does |
|---|---|---|
| Parameters | `fabric_kg = 0.25`, used by line e02 | Works out every formula again, on every calc. `result.parameters()` gives a sensitivity per parameter |
| Local override | electricity at 0.3 kg CO₂ per kWh, for this database | Gives the database its own copy of the process: 1.09205 |
| Scenario of a study | `study.calc(bg, scenario="clean-grid")`: the grid at 0.3, for everything | Changes the background process for everything in the result, through a low-rank update ([Edits and scenarios](#edits-and-scenarios)): 0.94205 |
| Cut-off | 0.01 kg of packaging tape, 3.85% of the mass | Skips the line. `result.provenance.cut_offs` lists it: 1.19205 |
| Flow with no factor | 0.002 kg of COD to water, acknowledged in `no_factor.csv` | Counts it 0. `result.not_characterised` lists it with its reason: 1.19205 |
| Database it uses | `uses: [dyes]`, and 0.01 kg of `dyes:reactive-dye` at 5.0596 per kg | Calculates the processes of `dyes` with the database's own, each with its own parameters and overrides ([design.md](design.md#databases-it-uses)): 1.242646 |

**Local overrides.** A database's purchases from an overridden process use its copy, and the copy buys from the background as it is, even from a process the database also overrides. Overriding electricity at 0.3 and fabric at 2.5 kWh per kg gives 0.25 × (2.1192 + 2.5 × 0.5298) + 0.5 × 0.3298 = 1.025825: the fabric copy buys the grid's electricity at 0.5298. The copy has its own id, `<database>:copy:<background id>`, so one id names one thing in results, sensitivity analyses and contribution trees: the copy of `toy:electricity-production` in the database `tshirt` is `tshirt:copy:toy:electricity-production`. A purchase from the overridden process names the copy as its supplier. In `contributions` and `sensitivity`, `copy_of` holds the id of the process copied, and the tree prints its name with `(local copy)`:

```python
print(hw.open(f"{toy}/tshirt-local-electricity/tshirt").calc(bg, "tshirt:tshirt").contributions())
# 1 unit of T-shirt: 1.09205 (100.0%), direct 0.00000
#   0.25 kg of cotton fabric production: 0.92715 (84.9%), direct 0.52980
#     0.75 kWh of electricity production: 0.39735 (36.4%), direct 0.39735
#   0.5 kWh of electricity production (local copy): 0.16490 (15.1%), direct 0.16490
```

**Scenarios** are a study's ([A study](#a-study)). Its parameter values and line changes are made first. A change to a line of a database the product reaches, its own or one it uses, changes that line. A change to a line of the background changes that process for everything: the T-shirt's own electricity, and the fabric's. A process that a database also overrides gets its copy from the changed process. `contributions`, `sensitivity` and `parameters` of the result are those of the scenario, and its provenance names it.

**Checks.** `calc` refuses, naming them: an override or scenario line that the background process does not have, read from the release's exchanges table for a release, and a database location that is not a code of the release's `geographies` table. A release built without geographies, or a folder, has no codes to check against. ecoinvent's `Geographies.xml` lists `GLO` and `RoW`: the 3.12 cut-off release holds 541 codes, and every location of its datasets is one of them.

**The pin.** `database.yaml` says which background the database was made for, as `background: name@version`. For a background read from a release, `calc` checks it first: the name must be the release's name, and the version at least 12 characters of its hash, as in `toy-background@3f9a1c2d4e5f`. Anything else raises `InvalidDataError`, for example a label such as `toy-background@latest` or the hash of another release. The error shows the pin, the release given and the line to write. A background read from a folder has no hash, so its pin is not checked. `indicator` may be left out when the method has one. The background is not solved again: each database process scores its direct emissions plus amount × stored score of what it buys. If the forward solve for what the database asks of the background fails its check with the factors in use, the background keeps the pivoted factors it falls back to, and every later solve uses them. The background also keeps its id index and the flows its databases look up, so a second `calc` does not build them again.

**`Result`** holds the score of one unit of what the process `process_id` makes:

| Attribute | Meaning |
|---|---|
| `score`, `unit`, `indicator`, `process_id` | 1.19205, `kg CO2-eq`, `climate-change`, `tshirt:tshirt` |
| `forward_score` | `h · s` from one forward solve: the same number by the other road |
| `provenance` | the database's pin as written in database.yaml (`pin`), input hash, engine version, method id, version and hash, the release it was read from (name and hash), solver settings, steps and residual, the condition estimate of the background's factors (of the edited matrix, in a scenario that changes A) and of the database block's, how many background scores are exact zeros, the scenario, the cut-offs, the databases it uses (`used`) and their cut-offs (`used_cut_offs`), the score's positive and negative parts with the flag `cancels` ([Scores that cancel](#factorize)), each method loaded (`methods`) and the one the indicator comes from (`indicator_method`), and for an indicator of a set the set (`nw_set`: id, version, hash, the method it pins, its unit and the method's indicators it leaves out) |
| `not_characterised` | flows the database reaches that have no factor in any indicator of the method, see below |

**`result.contributions(min_share=0.03, max_depth=3)`** returns the contribution tree. Every node adds up: score = direct + children + rest.

```python
root = result.contributions()
[(c.process_id, c.amount, c.unit, c.score) for c in root.children]
# [("toy:fabric-production", 0.25, "kg", 0.92715), ("toy:electricity-production", 0.5, "kWh", 0.2649)]
```

Each `Contribution` has `process_id`, `name`, `location`, `flow_id`, `amount`, `unit`, `per_unit`, `score`, `direct`, `rest` and `children`.

`print(root)`, or a bare `result.contributions()` in a notebook, shows the tree as text. Each line is the amount bought, the process, its score with its share of the node printed, and its direct emissions. A `rest` line shows what `min_share` left out, so every node adds up. A node at `max_depth` is a leaf: its score holds what it buys, and no rest line is printed under it, though its `rest` still holds those purchases:

```python
print(root)
# 1 unit of T-shirt: 1.19205 (100.0%), direct 0.00000
#   0.25 kg of cotton fabric production: 0.92715 (77.8%), direct 0.52980
#     0.75 kWh of electricity production: 0.39735 (33.3%), direct 0.39735
#   0.5 kWh of electricity production: 0.26490 (22.2%), direct 0.26490

print(result.contributions(min_share=0.5))
# 1 unit of T-shirt: 1.19205 (100.0%), direct 0.00000
#   0.25 kg of cotton fabric production: 0.92715 (77.8%), direct 0.52980
#     rest: 0.39735 (33.3%)
#   rest: 0.26490 (22.2%)
```

Shares are of the score of the node that is printed: `print(root.children[0])` shows the fabric as 100%. A score of exactly 0 is printed with no share.

**`root.to_dataframe()`** gives the tree as a pandas DataFrame, one row per process in the order it prints, the node first: `depth`, `process_id`, `name`, `location`, `amount`, `unit`, `score`, `share` (of the node's score, None for a score of 0), `direct` and `rest`. At each row, `direct`, `rest` and the scores of the rows one level below it add up to its `score`. pandas is not installed with headwater: `pip install 'headwater[pandas]'`, and without it the call raises `ImportError` saying so. `result.contributions(max_depth=1).to_dataframe()` for the T-shirt:

| depth | process_id | location | amount | unit | score | share | direct | rest |
|---|---|---|---|---|---|---|---|---|
| 0 | tshirt:tshirt | GLO | 1 | unit | 1.19205 | 1.0 | 0 | 0 |
| 1 | toy:fabric-production | GLO | 0.25 | kg | 0.92715 | 0.7778 | 0.5298 | 0.39735 |
| 1 | toy:electricity-production | GLO | 0.5 | kWh | 0.2649 | 0.2222 | 0.2649 | 0 |

The first row is the node itself, the total, so the table adds up as the tree does. A table of the inputs alone keeps the rows at depth 1: with `df = root.to_dataframe()`, `df[df.depth == 1]` is the fabric and the electricity, 0.92715 and 0.2649.

**`result.where(top=10)`** says which processes emit the score, wherever they are upstream, and in which locations. A process's part is its direct emissions times how much it runs for the database, so it counts every purchase that runs it. For the T-shirt, electricity runs 1.25 kWh: 0.5 for the T-shirt and 0.75 for its fabric.

```python
print(result.where())
# 1 unit of T-shirt: 1.19205
# By process:
#   1.25 kWh of electricity production (GLO): 0.66225 (55.6%)
#   0.25 kg of cotton fabric production (GLO): 0.52980 (44.4%)
# By location:
#   GLO: 1.19205 (100.0%), 2 processes
```

`contributions` shows the T-shirt's electricity as 0.26490, what the T-shirt buys; `where` shows 0.66225, everything electricity emits for it. It returns a `Where`: `processes`, a tuple of `Emitter`s (`process_id`, `name`, `location`, `amount`, `unit`, `score`, `copy_of`, and `database` for a process of a database it uses), where `amount` is how much the process makes or treats for the score, and `locations`, a tuple of `Place`s (`location`, `score`, `processes`), each largest first by size. Only the `top` largest of each are kept. The others are summed into `rest` and `locations_rest`, and counted in `rest_count` and `locations_rest_count`, so each grouping adds up to `score`. `score` is the forward road, h · s, the sum of every part: it equals `result.score` within the solve's tolerance. A process that emits nothing has no row and is not counted. `top=None` keeps every process that emits something. **`where.to_dataframe()`** gives the processes kept as a pandas DataFrame: `process_id`, `name`, `location`, `amount`, `unit`, `score` and `share`, which with `rest` add up to `score`.

**`result.direct_and_upstream()`** splits the score as Brightway does: `direct` is the database process's own row of `where(top=None)`, and `upstream` is the score minus it. The T-shirt emits nothing itself: (0, 1.19205). It needs no solve.

With the electricity moved to RER, the locations are RER 0.66225 (55.6%) and GLO 0.52980 (44.4%). A location is the code the process's record holds: a region is not split into the places it contains, and RoW is one location. The database's copy of a background process has its own row, marked `(local copy, GLO)`: for `tshirt-local-electricity`, fabric 0.52980, the grid's electricity 0.39735 for the fabric's 0.75 kWh, and the copy 0.16490 for the T-shirt's own 0.5 kWh. A treatment runs for the kg it treats: in `tshirt-waste`, incineration runs 0.05 kg and emits 0.06. `where` needs no new solve: it reads how much each process runs from the forward solve `calc` already did.

**`result.sensitivity(top=None, include_reference=False)`** is the sensitivity analysis: what a person can change, inputs, wastes, emissions and resources, largest elasticity first. A process's own reference line is how the process is defined, not a choice, so it is left out unless `include_reference=True`. For the T-shirt:

| Process | Line | Role | Elasticity | Sensitivity |
|---|---|---|---|---|
| tshirt:tshirt | e02 | input (0.25 kg fabric) | 0.7778 | 3.7086 |
| toy:electricity-production | e02 | emission (0.5 kg CO₂ per kWh) | 0.5243 | 1.25 |
| toy:fabric-production | e03 | emission (2.0 kg CO₂ per kg) | 0.4194 | 0.25 |
| toy:fabric-production | e02 | input (3 kWh per kg) | 0.3333 | 0.13245 |
| tshirt:tshirt | e03 | input (0.5 kWh) | 0.2222 | 0.5298 |

Sensitivity is the change of the score per one more unit of the line's amount. Elasticity is the % change per 1%. The elasticities of the emissions add up to 1, because the score is linear in them. Each `Sensitivity` names its process, `name` (`T-shirt`), and for a line bought or sent its supplier, `supplier_name` (`cotton fabric production`). A line of the database's copy of a background process has the copy's id and `copy_of` set to the id of the process copied. Lines are ranked as arrays, and a `Sensitivity` is made only for the lines returned. `sensitivity(top=10)` takes 28 ms on a synthetic background of ecoinvent size, and a call with no `top` also builds an object for every one of its 595,617 lines and takes 1.34 s (measured before `Sensitivity` gained `name` and `supplier_name`) ([benchmarks](appendix/benchmarks.md#database-queries-on-a-release)). A background read from a release keeps the amounts, signs and ids of its lines from the query that built A and B, so `sensitivity` reads nothing more from the release.

**Flows with no factor.** A flow with no factor for an indicator counts 0 for it. Carbon dioxide has no ozone depletion factor, so it adds nothing to that score. Two cases differ:

- A flow of the background that the method covers for no indicator is not an error, because ecoinvent has many such flows. `bg.not_characterised` lists all of them, with every process that uses them. `result.not_characterised` lists only the ones the database reaches, each with only the processes that run for the database. It is worked out when first read, from the forward solve that `calc` already did. `print(result)` ends with the number of them.
- A flow that the database itself emits and that the method covers for no indicator is an error of `validate` and of `calc`, the same `no-factor` error naming the flow and the database's processes, until a row of the database's `no_factor.csv` acknowledges it ([design.md](design.md#flows-with-no-factor)). With the row it counts 0, as in Brightway, and `result.not_characterised` lists it with `in_database` true and the row's `reason`. A flow that the background uses too is one entry, the database's own, naming the processes of both. The T-shirt with 0.002 kg of COD to water and its row scores 1.19205. A row that is not needed is a warning of `validate`, and `calc` does not refuse it.

**`result.parameters(top=None)`** lists every named parameter of the database, largest elasticity first, as `ParameterSensitivity`s: its id, value, formula, unit, sensitivity and elasticity. For the T-shirt whose fabric line says `fabric_kg`:

| Parameter | Value | Sensitivity | Elasticity |
|---|---|---|---|
| fabric_kg | 0.25 kg | 3.7086 | 0.7778 |

It needs no solve. Each line whose amount is a formula has a sensitivity, as for `sensitivity`, times the unit factor of the line (0.001 for a line in g of a flow in kg). The chain rule passes it on to the parameters the formula uses, times the formula's exact derivative, worked out from the formula itself, not by a finite difference. Then each parameter that is a formula passes its own on in the same way, so `fabric_gross = fabric_kg * (1 + loss)` passes 1.05 times its sensitivity on to `fabric_kg`. A formula parameter's own sensitivity holds the parameters it is computed from as they are, as a scenario setting its value would. At a kink, such as `max(a, b)` with a = b, the derivative is that of the branch the formula takes. A derivative that is not a finite number, such as `sqrt(x)` at 0, raises a calc error naming the line. A parameter no line uses has a sensitivity of 0. In the scenario `more-fabric`, where `fabric_kg` is 0.30, the score is 1.37748 and the elasticity 0.8077.

**Cut-offs.** A line cut off buys nothing. `result.provenance.cut_offs` holds its issue, with the reason, criterion, share and cumulative share, and `cut_off_shares` the cumulative share by criterion: `{mass: 0.0385}` for the T-shirt's packaging tape.

### A background product

**`bg.score(process_id, *, indicator=None)`** is the stored score of one unit of what a process makes, with no solve: `bg.score("toy:fabric-production")` is 3.7086, as Brightway's `lca.score` after `redo_lcia` for 1 kg of fabric. For a waste treatment it is per unit treated, a positive burden: 1.2 for `toy:incineration-textile-waste`.

**`bg.contributions(process_id, *, indicator=None, min_share=0.03, max_depth=3)`**, **`bg.where(process_id, *, indicator=None, top=10)`** and **`bg.sensitivity(process_id, *, indicator=None, top=None, include_reference=False)`** answer the same three questions for one unit of a product of the background, with no database. `process_id` is the id of the process that makes it. For 1 kg of cotton fabric:

```python
print(bg.contributions("toy:fabric-production"))
# 1 kg of cotton fabric production: 3.70860 (100.0%), direct 2.11920
#   3 kWh of electricity production: 1.58940 (42.9%), direct 1.58940
```

For a waste treatment the unit is 1 kg treated, and the score is the positive burden per kg: `bg.contributions("toy:incineration-textile-waste").score` is 1.2.

**`bg.direct_and_upstream(process_id, *, indicator=None)`** splits the score as Brightway does, where `direct` is the process's column of the characterised inventory: its own row of `where(process_id, top=None)`, everything it emits for the unit, and `upstream` is `bg.score(process_id)` minus that. For 1 kg of cotton fabric it is (2.1192, 1.5894). It differs from `contributions(process_id).direct` only when the process feeds itself through a loop: if each kWh of electricity bought 0.01 kg of fabric, the fabric would run 1.03 times for 1 kg, and its Brightway direct would count all of them, while the tree's `direct` counts the 1 kg it starts from and puts the rest under its electricity. A process that buys its own product directly is the same in both. It takes one forward solve, as `where`. `contributions` reads the stored scores and solves nothing. `where` and `sensitivity` each do one forward solve for how much each process runs, 2.4 ms on ecoinvent ([benchmarks](appendix/benchmarks.md#calculation-core)), and keeps pivoted factors if that solve needs them. For 1 kg of fabric, which runs electricity for 3 kWh:

| Process | Line | Role | Elasticity | Sensitivity |
|---|---|---|---|---|
| toy:fabric-production | e03 | emission (2.0 kg CO₂ per kg) | 0.5393 | 1.0 |
| toy:fabric-production | e02 | input (3 kWh per kg) | 0.4286 | 0.5298 |
| toy:electricity-production | e02 | emission (0.5 kg CO₂ per kWh) | 0.4045 | 3.0 |
| toy:fabric-production | e04 | emission (0.004 kg CH₄ per kg) | 0.0321 | 29.8 |
| toy:electricity-production | e03 | emission (0.001 kg CH₄ per kWh) | 0.0241 | 89.4 |

`print(bg.where("toy:fabric-production"))`:

```
1 kg of cotton fabric production: 3.70860
By process:
  1 kg of cotton fabric production (GLO): 2.11920 (57.1%)
  3 kWh of electricity production (GLO): 1.58940 (42.9%)
By location:
  GLO: 3.70860 (100.0%), 2 processes
```

On ecoinvent 3.12 cut-off with EF 3.1, the top 10 processes of 10 products, for climate change and land use, are the ones Brightway's contribution analysis lists, in the same order, with every value within 10⁻⁶ ([benchmarks](appendix/benchmarks.md#top-contributors-against-brightway)).

An id the background does not hold raises `ValueError` with the closest id. `sensitivity` of a product whose score is exactly 0 raises a calc error naming it: elasticities are relative to the score. `indicator` is chosen as for `database.calc`.

### Finding a process

**`bg.find(text, *, location=None, product=None, kind=None, limit=20)`** returns the processes that match `text`, best first, as `Match`es: `process_id`, `flow_id`, `name`, `reference_product`, `location`, `unit`, `kind` and `why`. The `process_id` is the supplier a database's line names, and `flow_id` its flow: `new_exchange("input", match.flow_id, 0.5, match.unit, supplier=match.process_id)`. On the toy, `bg.find("electricity")` is one match: `toy:electricity-production`, `electricity production | electricity | GLO`, kWh, `why` = `name starts with`.

There are six rules, in this order. A process is listed once, at the first rule it meets:

| Rule (`why`) | It matches when |
|---|---|
| `name is` | the name is the text |
| `name starts with` | the name starts with the text |
| `every word in name` | the name has every word of the text, in any order, each as a part of a word: `steel low-alloyed` finds `market for steel, low-alloyed`, and `textile wa` finds `incineration of textile waste` |
| `product is`, `product starts with`, `every word in product` | the same three, on the name of the reference product |

Case is ignored. Nothing is fuzzy: no synonyms and no spelling repair. Words are split on spaces. Ties go by name (without case), then location, product and id, so the same text always gives the same list. `location` keeps the processes in one place, by its whole code (`RER`, not `RE`). `product` keeps those whose reference product has every word of it, and `kind` those of one `ProcessKind`, such as `market_activity`. `limit=None` returns every match. No text, a negative limit, a `kind` that does not exist and a `location` that no process has raise `ValueError` naming the choices (`GL0` suggests `GLO`): a typo is an error, not an empty list.

**`bg.get_process(name, *, product=None, location=None)`** is the one process with that name, and that reference product and location when given, as Brightway's `get_node(name=..., product=..., location=...)`. Each is matched exactly, case included, with `why` = `name is`: `bg.get_process("electricity production", product="electricity", location="GLO")` is the `Match` of `toy:electricity-production`. When no process fits it raises `ValueError` naming the processes of that name, or the closest names `find` gives (`Cotton fabric production` names `cotton fabric production`); when several fit, it names them and asks for `product` or `location`.

The table behind it is built in memory on the first call, from the processes and flows the background holds, and kept for the next. There is no index file, so there is nothing to rebuild and nothing to go stale. The command `headwater find` (see [workflows.md](workflows.md)) needs no database and no method: it reads the release's `processes` and `flows` tables directly, and solves nothing. On ecoinvent 3.12 the table takes 48 ms to build, and a query with the default limit takes a median of 2 to 11 ms ([benchmarks](appendix/benchmarks.md#finding-a-process)).

### Names of units and indicators

A unit is written as its source writes it, in a release, a result and every table: ecoinvent's `metric ton*km`, where Brightway says `ton kilometer`. headwater does not rename source data, so a release keeps its source's names and its hash. The Excel import reads Brightway's names as well as these ([design.md](design.md#excel-import)). The units of the flows of ecoinvent 3.12, and the name Brightway gives each (bw2io 0.9.14's `normalize_units`):

| headwater and ecoinvent | Brightway |
|---|---|
| `kg` | kilogram |
| `unit` | unit |
| `m3` | cubic meter |
| `metric ton*km` | ton kilometer |
| `m2` | square meter |
| `ha` | hectare |
| `hour` | hour |
| `km` | kilometer |
| `MJ` | megajoule |
| `kWh` | kilowatt hour |
| `m` | meter |
| `person*km` | person kilometer |
| `l` | litre |
| `m*year` | meter-year |
| `m2*year` | square meter-year |
| `m3*year` | cubic meter-year |
| `km*year` | kilometer-year |
| `kg*day` | kilogram day |
| `guest night` | guest night |
| `kBq` | kilo Becquerel |
| `Sm3` | standard cubic meter |
| `EUR2005` | EUR2005 |

An indicator has two names, as ISO 14044 gives them: the impact category, `name`, and the category indicator, `category_indicator`, what its score measures. Brightway's method `('ecoinvent-3.12', 'EF v3.1', 'climate change', 'global warming potential (GWP100)')` is the indicator `climate-change` of the method `EF-3.1`, named `climate change`, with the category indicator `global warming potential (GWP100)`. `bg.indicators` gives each indicator's `id`, `name`, `category_indicator` and `unit`. ecoinvent's LCIA workbook gives the category indicator of every category. SimaPro CSV and openLCA JSON-LD name the category only, and the toy method has none: there `category_indicator` is None.

### Indicator vectors

**`bg.write_indicators(*, name=None, into=None)`** freezes the score vector of every indicator into a new indicators release and returns it. It goes beside the release the background was read from, or into `into`. It pins its source release and the method by hash, and the source release is not touched. With several methods it holds each, with its own `indicators/<method>.parquet`, and is named `<release>.EF-3.1.IPCC-2021`. The same background and method give the same release when solved again on the same machine.

For the toy, `indicators/toy-climate.parquet` holds:

| product_id | climate-change |
|---|---|
| toy:electricity-production | 0.5298 |
| toy:fabric-production | 3.7086 |
| toy:incineration-textile-waste | 1.2 |

Each score is per unit of what the process makes, or per unit of waste it treats. So the incineration of textile waste scores +1.2 per kg treated, a positive burden, and 0.05 kg sent adds 0.06. Inside the matrix a treatment's reference is -1, and the transposed solve gives it -1.2. The file holds that number times the sign of the process's reference, so every consumer multiplies a positive amount by the score whatever the product is. The release also records how the scores were solved: the settings, the steps of each solve and the largest residual.

### Many SKUs

**`hw.score_many(source, table, *, indicators=None)`** scores a table of SKUs in one go, by one sparse multiply with the stored vectors: many products at once, as Brightway's `MultiLCA` does, with no solve. The table has the columns `sku`, `product_id` and `amount`, one row per product a SKU buys, as a PyArrow table or a dict of columns:

```python
skus = hw.score_many(bg, {
    "sku": ["a", "a", "a", "b"],
    "product_id": ["toy:fabric-production", "toy:electricity-production",
                   "toy:incineration-textile-waste", "toy:electricity-production"],
    "amount": [2.0, 1.0, 0.1, 10.0],
})
skus["climate-change"]  # array([8.067, 5.298]): 2 × 3.7086 + 1 × 0.5298 + 0.1 × 1.2, and 10 × 0.5298
```

The `product_id` is the id of the process that makes the product, and the amount is in the reference unit of its flow. For a waste treatment it is the kg sent. A SKU and product that appear twice add up. An unknown product, or an amount that is not a finite number, raises `InvalidDataError` naming the SKU and the product. `source` is a background, or an indicators release, which needs no factorization: its stored vectors are all that is read. The result is a `ManyScores` with `skus` (in order of first appearance), `indicators`, `units`, `scores` (one row per SKU, one column per indicator), `to_arrow()`, and the release and method hashes it came from.

Times for every step above are in [appendix/benchmarks.md](appendix/benchmarks.md#calculation-from-a-release).

`diff` between git refs is a command (`headwater diff`, see [workflows.md](workflows.md)).

### A study

A study names products of databases with amounts, and asks one question of them ([design.md](design.md#studies)). `hw.open` of a study folder returns a `Study`, and `hw.Study(products=..., methods=...)` makes one in memory:

```python
study = hw.open(f"{toy}/tshirt-dyed/tshirt-footprint")
[tshirt] = study.calc(bg)                        # 1.242646
[cleaner] = study.calc(bg, scenario="cleaner-dye")  # 1.237646: a line of the used database dyes
two = hw.open(f"{toy}/tshirt-dyed/tshirt-and-dye")
two.scores(bg)  # {"tshirt:tshirt": {"climate-change": 2.485292}, "dyes:reactive-dye": {"climate-change": 2.5298}}

memory = hw.Study(products={"tshirt:tshirt": 1}, methods=["toy-climate"],
                  databases=f"{toy}/tshirt-dyed",
                  scenarios={"cleaner-dye": {"changes": [
                      {"process": "dyes:reactive-dye", "line": "e03", "amount": 3.5}]}})
memory.calc(bg)[0].provenance.study  # StudyRef(name=None, saved=False, commit=None, changed=False)
memory.save(f"{toy}/tshirt-dyed/my-footprint", goal="...", functional_unit="1 T-shirt",
            boundary="cradle-to-gate", cut_off={"criteria": ["mass"], "max_cumulative": 0.03})
```

| Name | What it is |
|---|---|
| `calc(bg, *, indicator=None, scenario=None)` | One `Result` per product, in the order of `products`, each for its amount: the amount is scored in the system, so `contributions` adds up to it, and `result.amount` says it |
| `scores(bg, *, indicators=None, scenario=None)` | `{process id: {indicator: score}}`, each for its amount |
| `validate(bg)` | The study's own rules, and every database its products reach, with its scenarios |
| `save(path, *, goal, functional_unit, boundary, cut_off=None, **optional)` | Writes study.yaml and the scenario files next to the databases, and returns the study as saved |
| `name`, `products`, `folder` | `tshirt-footprint` (None in memory); the `Product`s, each a `process` and an `amount`; the records read |

A product of a database is scored as `database.calc` scores it, for its amount, with the study's scenario and its own no_factor rows. A product of the background is scored from its stored score. In a scenario that changes a background line it is refused, for now; a scenario that changes only the lines and parameters of databases leaves it as it is. Each result's `provenance.study` names the study: `saved`, `commit` (the commit the repository that holds it is at, or None), and `changed`, true when the study or a database it reaches differs from that commit. Its `input_hash` is the study's products and amounts, methods, own no_factor rows, the product, the scenario, the records of every database it reaches and the background: the study in memory and the same study saved hash alike. A product of a database scored on its own, `database.calc`, names the database instead, in `provenance.database`: `name`, `commit` and `changed`, true when the database or a database it uses differs from that commit, checked at the database's first calc.

### Building and editing a database

A Brightway user builds an inventory with `new_activity`, `new_exchange(...).save()` and `act.copy()`. headwater has the same calls, and they change the database's own files ([design.md](design.md#editing-from-python) has the rules). `hw.Database.new(path, bg, ...)` starts a database in an empty folder, and `hw.open(path).edit(bg)` edits one. Both return a `DatabaseEditor`. Nothing is written until `save()`, which checks the whole database against `bg`, with the databases it uses and those next to it that use it, writes only the files that changed, and returns the database as saved:

```python
editor = hw.open("tshirt").edit(bg)
editor.override("toy:electricity-production", file="own-wind-power").set_exchange(
    "e02", amount=0.3, comment="Wind power: 0.3 kg CO2 per kWh instead of 0.5")
database = editor.save()  # writes overrides/own-wind-power.csv and nothing else
database.calc(bg, "tshirt:tshirt").score  # 1.09205, the local override
```

| Brightway | headwater | File changed |
|---|---|---|
| `bd.Database("tshirt")` | `hw.Database.new(path, bg)`, or `hw.open(path).edit(bg)` | `database.yaml` |
| changing the database's metadata | `editor.set(description=..., uses=["dyes"])` | `database.yaml`, the field's line |
| `db.new_activity(code, name=..., location=..., unit=...)` | `editor.new_process(code, name=..., location=..., unit=..., product=...)` | a row of `processes.csv` and one of `exchanges.csv`, its reference line e01, each at its sorted place, and a row of `flows.csv` |
| `db.get(code)` | `editor.process("tshirt")` or `editor.process("tshirt:tshirt")` | |
| `act["name"] = ...; act.save()` | `process.set(name=..., location=...)` | its row of `processes.csv` |
| `act.delete()` | `editor.remove_process("tshirt")` | its rows of both tables |
| `act.new_exchange(input=..., amount=..., type=...).save()` | `process.new_exchange(role, flow, amount, unit, supplier=...)`, which returns the line id, `e04` | a row of `exchanges.csv`, after the process's last line |
| `exc["amount"] = 0.3; exc.save()` | `process.set_exchange("e02", amount=0.3)`; `""` empties a cell | that row |
| `exc.delete()` | `process.remove_exchange("e02")` | that row |
| `act.copy()`, then changing its exchanges and relinking | `editor.override(process_id).set_exchange("e02", amount=0.3)`, `.reset_exchange("e02")` | a row of `overrides/`, no relinking |
| `parameters.new_project_parameters`, `new_activity_parameters` | `editor.set_parameter("fabric_kg", 0.25, process_id=None, unit=..., comment=...)`, `editor.remove_parameter(...)` | `parameters.yaml`, the lines that changed |
| none: Brightway drops an exchange with no supplier | `editor.cut_off(process_id, line, criterion=..., share=..., reason=..., who=..., when=...)`, `editor.remove_cut_off(...)` | `cut_offs.csv` |
| none | `editor.new_flow(code, name=..., kind=..., unit=...)` | `flows.csv` |

- An exchange is named by its line id, `e02`, the `line` column of exchanges.csv.
- `role` is always given, never read from the sign of the amount ([Roles and signs](design.md#roles-and-signs)). An amount is a number or a formula of the database's parameters, as in exchanges.csv.
- `process.exchanges` gives each exchange as written in exchanges.csv, column by column, without its `process` column, with its line id in `line`. `editor.changed` gives the files `save` would write.
- A local override cannot add or remove lines, or change the reference line, where Brightway's copy can: a copy is the original with some lines changed ([design.md](design.md#overrides)).
- `Database.new(path, bg, *, name=None, pin=None, methods=(), uses=(), description=None)` needs only the folder and the background. `name` is the folder's name by default, and `methods` lists the methods the database is scored with by default, none when left out. One purchase is three calls and `save()`:

```python
database = hw.Database.new("shirt", bg, pin="toy-background@fixture")
shirt = database.new_process("shirt", name="T-shirt", location="GLO", product="T-shirt", unit="unit")
shirt.new_exchange("input", "toy:cotton-fabric", 0.25, "kg", supplier="toy:fabric-production")
database.save().calc(bg, "shirt:shirt").score  # 0.92715
```

- `pin` is needed for a background read from a folder, which has no hash. For a release it is the release's name and the first 12 characters of its hash.
