"""Every kind of product line in an ecoSpold2 folder, with the role its system model's table gives it.

A kind is a line's side (reference or input), by-product class and sign, or its exchange groups
when it has no side. For each kind: how many lines, the role (none when the table rejects it), how
many name a supplier (`activityLinkId`), how many name themselves, and the kind of the supplier's
reference line, or that no file makes it. Elementary lines are counted by their groups.

    uv run python bench/ecospold2_line_kinds.py <folder> results/<name>_results.json [--workers 2]

The folder holds `datasets/`. Counts only go to the results file. The first files of each kind the
table rejects are printed, for a person to open, and not written anywhere.
"""

import argparse
import json
import os
import platform
import sys
import time
from collections import Counter
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from headwater.io.ecospold2.dataset import (
    ROLES,
    SYSTEM_MODELS,
    RoleTable,
    Side,
    Sign,
    side_of,
    sign_of,
)
from headwater.io.ecospold2.masterdata import NS, by_product_class, number, parse_xml, text

HERE = Path(__file__).resolve().parent
EXAMPLES = 3

type Kind = tuple[str, str, str]
"""(side or groups, class, sign)."""


def read_file(path: Path) -> dict[str, object]:
    """What one file says: its system model, its reference kind and its lines."""
    root = parse_xml(path)
    dataset = root.find(f"{NS}activityDataset")
    if dataset is None:
        dataset = root.find(f"{NS}childActivityDataset")
    assert dataset is not None, path
    model = dataset.find(f"{NS}modellingAndValidation/{NS}representativeness")
    system_model = None if model is None else model.get("systemModelId")
    flow_data = dataset.find(f"{NS}flowData")
    assert flow_data is not None, path
    products, elementary = [], []
    for element in flow_data:
        output, input_ = text(element, "outputGroup"), text(element, "inputGroup")
        if element.tag == f"{NS}elementaryExchange":
            elementary.append(f"output {output}, input {input_}")
            continue
        if element.tag != f"{NS}intermediateExchange":
            continue
        amount = number(element.get("amount"))
        side = side_of(output, input_)
        where = side.value if side is not None else f"output group {output}, input group {input_}"
        link = element.get("activityLinkId")
        supplier = f"{link}_{element.get('intermediateExchangeId')}" if link else None
        products.append(
            (
                where,
                str(by_product_class(element)),
                "not a number" if amount is None else sign_of(amount).value,
                supplier,
            )
        )
    return {
        "file": path.name,
        "system_model": system_model,
        "products": products,
        "elementary": elementary,
    }


def survey(folder: Path, workers: int) -> tuple[dict[str, object], dict[Kind, list[str]]]:
    files = sorted(p for p in (folder / "datasets").iterdir() if p.suffix == ".spold")
    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            read = list(pool.map(read_file, files, chunksize=256))
    else:
        read = [read_file(p) for p in files]
    models = Counter(str(r["system_model"]) for r in read)
    if len(models) != 1:
        sys.exit(f"the folder mixes system models: {dict(models)}")
    [model_id] = models
    name = SYSTEM_MODELS.get(model_id)
    if name is None:
        sys.exit(f"system model {model_id} has no role table")
    table = ROLES[name]
    reference: dict[str, Kind] = {}
    for r in read:
        stem = str(r["file"]).removesuffix(".spold")
        for kind in r["products"]:  # type: ignore[union-attr]
            if kind[0] == "reference":
                reference[stem] = kind[:3]
    lines: Counter[Kind] = Counter()
    linked: Counter[Kind] = Counter()
    itself: Counter[Kind] = Counter()
    suppliers: dict[Kind, Counter[str]] = {}
    examples: dict[Kind, list[str]] = {}
    for r in read:
        stem = str(r["file"]).removesuffix(".spold")
        for where, flow_class, sign, supplier in r["products"]:  # type: ignore[union-attr]
            kind = (where, flow_class, sign)
            lines[kind] += 1
            if supplier is not None:
                linked[kind] += 1
                if supplier == stem and where != "reference":
                    itself[kind] += 1
                found = reference.get(supplier)
                label = "no file makes it" if found is None else f"{found[1]}, {found[2]}"
                suppliers.setdefault(kind, Counter())[label] += 1
            if _role(table, kind) is None and len(examples.setdefault(kind, [])) < EXAMPLES:
                examples[kind].append(str(r["file"]))
    elementary = Counter(e for r in read for e in r["elementary"])  # type: ignore[union-attr]
    kinds = [
        {
            "side": where,
            "class": flow_class,
            "sign": sign,
            "lines": lines[(where, flow_class, sign)],
            "role": _role(table, (where, flow_class, sign)),
            "with_supplier": linked[(where, flow_class, sign)],
            "supplier_is_itself": itself[(where, flow_class, sign)],
            "supplier_reference": dict(
                sorted(suppliers.get((where, flow_class, sign), {}).items())
            ),
        }
        for where, flow_class, sign in sorted(lines)
    ]
    found = {
        "folder": folder.name,
        "system_model": name,
        "files": len(read),
        "product_lines": sum(lines.values()),
        "rejected_lines": sum(k["lines"] for k in kinds if k["role"] is None),  # type: ignore[misc]
        "kinds": kinds,
        "elementary": dict(sorted(elementary.items())),
    }
    return found, {k: v for k, v in examples.items() if v}


def _role(table: RoleTable, kind: Kind) -> str | None:
    where, flow_class, sign = kind
    try:
        key = (Side(where), flow_class, Sign(sign))
    except ValueError:
        return None
    found = table.get(key)
    if found is None:
        return None
    role, negated = found
    return f"{role.value}, amount negated" if negated else role.value


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("folder", type=Path)
    parser.add_argument("results", type=Path)
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args(argv)
    start = time.perf_counter()
    load = [round(v, 2) for v in os.getloadavg()]
    found, examples = survey(args.folder.expanduser(), args.workers)
    found["seconds"] = round(time.perf_counter() - start, 1)
    found["machine"] = f"{platform.machine()} {platform.system()} {platform.release()}"
    found["load_average_at_start"] = load
    args.results.write_text(json.dumps(found, indent=2) + "\n")
    for k in found["kinds"]:  # type: ignore[union-attr]
        mark = "REJECTED" if k["role"] is None else k["role"]
        print(
            f"{k['lines']:>9,}  {k['side']:<10} {k['class']:<20} {k['sign']:<9} {mark:<35} "
            f"supplier {k['with_supplier']:,} (itself {k['supplier_is_itself']:,}) "
            f"{k['supplier_reference']}"
        )
    print(f"elementary: {found['elementary']}")
    for kind, files in examples.items():
        print(f"rejected {kind}: {', '.join(files)}")


if __name__ == "__main__":
    main()
