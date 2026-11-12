"""Pure calculation functions on NumPy arrays and SciPy sparse matrices.

Matrix indices exist only inside one calculation. No file access, no ids, no global state.
"""

from headwater.calc.errors import CalculationError, ResidualError, SingularMatrixError
from headwater.calc.explain import (
    Levers,
    LineEffects,
    Node,
    emission_levers,
    emission_line_levers,
    explain,
    levers,
    line_levers,
    where,
)
from headwater.calc.signs import Entry, Matrix, RoleEntry, matrix_entry, role_entries
from headwater.calc.skus import sku_scores
from headwater.calc.solve import (
    CONDITION_LIMIT,
    TOLERANCE,
    Factorization,
    Solution,
    SolverSettings,
    Step,
    factorize,
    scores,
    supply,
)
from headwater.calc.study import StudySolution, solve_study
from headwater.calc.update import (
    LowRankUpdate,
    low_rank_update,
    update_column,
    update_emissions,
)
from headwater.calc.weighting import weighted_scores, weighted_sum

__all__ = [
    "CONDITION_LIMIT",
    "TOLERANCE",
    "CalculationError",
    "Entry",
    "Factorization",
    "Levers",
    "LineEffects",
    "LowRankUpdate",
    "Matrix",
    "Node",
    "ResidualError",
    "RoleEntry",
    "SingularMatrixError",
    "Solution",
    "SolverSettings",
    "Step",
    "StudySolution",
    "emission_levers",
    "emission_line_levers",
    "explain",
    "factorize",
    "levers",
    "line_levers",
    "low_rank_update",
    "matrix_entry",
    "role_entries",
    "scores",
    "sku_scores",
    "solve_study",
    "supply",
    "update_column",
    "update_emissions",
    "weighted_scores",
    "weighted_sum",
    "where",
]
