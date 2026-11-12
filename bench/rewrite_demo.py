import bw2data as bd
import bw2calc as bc

bd.projects.set_current("rewrite-demo")

bd.Database("biosphere").write({
    ("biosphere", "co2"): {"name": "Carbon dioxide, fossil", "unit": "kilogram", "type": "emission"},
    ("biosphere", "ch4"): {"name": "Methane, fossil", "unit": "kilogram", "type": "emission"},
})

bd.Database("tshirt").write({
    ("tshirt", "tshirt"): {
        "name": "T-shirt production", "unit": "unit", "location": "BD",
        "exchanges": [
            {"input": ("tshirt", "tshirt"), "amount": 1, "type": "production"},
            {"input": ("tshirt", "fabric"), "amount": 0.25, "type": "technosphere"},
            {"input": ("tshirt", "elec"), "amount": 0.5, "type": "technosphere"},
        ],
    },
    ("tshirt", "fabric"): {
        "name": "cotton fabric production", "unit": "kilogram", "location": "IN",
        "exchanges": [
            {"input": ("tshirt", "fabric"), "amount": 1, "type": "production"},
            {"input": ("tshirt", "elec"), "amount": 3, "type": "technosphere"},
            {"input": ("biosphere", "co2"), "amount": 2.0, "type": "biosphere"},
            {"input": ("biosphere", "ch4"), "amount": 0.004, "type": "biosphere"},
        ],
    },
    ("tshirt", "elec"): {
        "name": "electricity production", "unit": "kilowatt hour", "location": "IN",
        "exchanges": [
            {"input": ("tshirt", "elec"), "amount": 1, "type": "production"},
            {"input": ("biosphere", "co2"), "amount": 0.5, "type": "biosphere"},
            {"input": ("biosphere", "ch4"), "amount": 0.001, "type": "biosphere"},
        ],
    },
})

bd.Method(("demo", "GWP100")).write([
    (("biosphere", "co2"), 1),
    (("biosphere", "ch4"), 29.8),
])


def score():
    lca = bc.LCA({bd.get_node(database="tshirt", code="tshirt"): 1}, method=("demo", "GWP100")); lca.lci(); lca.lcia()
    return round(lca.score, 5)
print("1. fresh:", score())
bd.Database("biosphere").write(bd.Database("biosphere").load())
print("2. after rewriting biosphere with identical data:", score())
bd.Database("tshirt").process()
print("3. after reprocessing tshirt (what any edit triggers):", score())
