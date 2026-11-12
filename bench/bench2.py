import json, sys, time, resource
import bw2data as bd, bw2calc as bc

def main():
    out = {}
    def mark(k, t0):
        out[k] = round(time.perf_counter() - t0, 3); print(k, out[k], "s", flush=True)
    bd.projects.set_current("ei312-bench")
    method = ("bench", "GWP100 subset")
    db = bd.Database("ei312-cutoff")
    node = db.search("textile, woven cotton")[0]
    print("node", node["name"], node["location"])

    t = time.perf_counter(); lca = bc.LCA({node: 1}, method=method); mark("lca_init_load_packages", t)
    t = time.perf_counter(); lca.load_lci_data(); mark("build_matrices", t)
    t = time.perf_counter(); lca.decompose_technosphere(); mark("factorize_scipy", t)
    t = time.perf_counter(); lca.lci(); mark("lci_after_factorize", t)
    t = time.perf_counter(); lca2 = bc.LCA({node: 1}, method=method); lca2.lci(); lca2.lcia(); mark("full_lca_no_factorize", t)
    base = lca2.score

    exc = next(e for e in node.technosphere())
    print("editing", exc.input["name"], exc["amount"])
    t = time.perf_counter(); exc["amount"] = exc["amount"] * 1.1; exc.save(); mark("edit_one_exchange_save", t)
    print("dirty?", bd.databases["ei312-cutoff"].get("dirty"))
    t = time.perf_counter(); lca3 = bc.LCA({node: 1}, method=method); lca3.lci(); lca3.lcia(); mark("lca_after_one_edit", t)
    print("score before/after", base, lca3.score)
    t = time.perf_counter(); bd.projects.copy_project("ei312-bench-copy", switch=False); mark("copy_project", t)
    out["peak_rss_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6)
    json.dump(out, open(sys.argv[1], "w"), indent=2)

if __name__ == "__main__":
    main()
