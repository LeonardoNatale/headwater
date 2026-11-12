import bw2data as bd
import bw2calc as bc

bd.projects.set_current("tshirt-demo")

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

lca = bc.LCA({bd.get_node(database="tshirt", code="tshirt"): 1}, method=("demo", "GWP100"))
lca.lci()
lca.lcia()
print("A=\n", lca.technosphere_matrix.toarray())
print("B=\n", lca.biosphere_matrix.toarray())
print("supply s =", lca.supply_array)
print("inventory g =", lca.inventory.sum(axis=1).A1)
print("score =", lca.score)
print("activity_dict", dict(lca.dicts.activity))
