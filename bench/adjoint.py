import random, time
import numpy as np
from scipy.sparse.linalg import spsolve
import bw2data as bd, bw2calc as bc

def main():
    bd.projects.set_current("ei312-bench")
    method = ("bench", "GWP100 subset")
    acts = list(bd.Database("ei312-cutoff"))
    lca = bc.LCA({acts[0]: 1}, method=method)
    lca.load_lci_data(); lca.load_lcia_data()
    A, B, C = lca.technosphere_matrix, lca.biosphere_matrix, lca.characterization_matrix
    t = time.perf_counter()
    c = np.asarray((C @ B).sum(axis=0)).ravel()      # impact per unit of each process's direct emissions
    x = spsolve(A.T.tocsc(), c)                       # one transposed solve: score per unit of every product
    dt = time.perf_counter() - t
    print(f"adjoint: {len(x)} scores in {dt:.2f}s")
    random.seed(7)
    errs = []
    for a in random.sample(acts, 20):
        lca.lci({a.id: 1}); lca.lcia_calculation()
        ref = lca.score
        mine = x[lca.dicts.product[a.id]]
        errs.append(abs(mine - ref) / max(abs(ref), 1e-12))
    print(f"max relative diff vs bw2calc over 20 products: {max(errs):.2e}")

if __name__ == "__main__":
    main()
