"""Post-measurement data analysis and Van der Pauw calculations.

Key functions:
- odd_resistance(): Extract resistance from positive/negative current pair
- solve_sheet_resistance(): Bisection solver for Van der Pauw equation
- reciprocity_error(): Assess ||R(+I) - R(-I)|| / R for polarity balance
- rotation_spread(): Assess anisotropy from multi-contact measurements
- vdp_asymmetry(): Van der Pauw method validation metrics

The sheet resistance solver uses:
1. Van der Pauw equation: π*Rs/(ln 2) * F(ρ) = R_measured
2. Bisection search over Rs range [1 mΩ/sq, 1 GΩ/sq]
3. Tolerance of 1e-6 for convergence
4. Tracks iterations and residual for diagnostics
"""

from __future__ import annotations

from dataclasses import dataclass
from math import exp, isfinite, pi
from statistics import mean, pstdev


@dataclass(frozen=True)
class SheetResistanceResult:
    value_ohm_per_sq: float
    converged: bool
    iterations: int
    residual: float
    r_a_ohm: float
    r_b_ohm: float
    r_a_over_r_b: float
    approximation_ohm_per_sq: float


def odd_resistance(
    voltage_positive_v: float,
    voltage_negative_v: float,
    current_positive_a: float,
    current_negative_a: float,
) -> float:
    """Return odd-in-current resistance using measured current values."""
    delta_i = current_positive_a - current_negative_a
    if delta_i == 0:
        raise ValueError("positive and negative current readings must differ")
    return (voltage_positive_v - voltage_negative_v) / delta_i


def solve_sheet_resistance(
    r_a_ohm: float,
    r_b_ohm: float,
    *,
    relative_tolerance: float = 1e-9,
    residual_tolerance: float = 1e-12,
    max_iterations: int = 200,
) -> SheetResistanceResult:
    """Solve exp(-pi*Ra/Rs) + exp(-pi*Rb/Rs) = 1 by bisection."""
    _validate_positive_finite("r_a_ohm", r_a_ohm)
    _validate_positive_finite("r_b_ohm", r_b_ohm)

    # Van der Pauw linear approximation: Rs ≈ (π/ln(2)) * mean(Ra, Rb)
    approximation = pi / _ln2() * mean([r_a_ohm, r_b_ohm])

    # Bracket the root: set initial bounds with safety factors
    # Low bound: 1e-12 of the smaller resistance
    # High bound: 10× the linear approximation
    low = 1e-12 * min(r_a_ohm, r_b_ohm)
    high = 10.0 * pi / _ln2() * max(r_a_ohm, r_b_ohm)

    # Define Van der Pauw equation to solve: f(Rs) = 0
    # where f(Rs) = exp(-π*Ra/Rs) + exp(-π*Rb/Rs) - 1
    def f(rs: float) -> float:
        return exp(-pi * r_a_ohm / rs) + exp(-pi * r_b_ohm / rs) - 1.0

    # Expand high bound until f(high) < 0 (captures the root in interval [low, high])
    while f(high) < 0:
        high *= 2.0

    # Bisection search: iteratively narrow the interval until convergence
    iterations = 0
    mid = high
    residual = f(mid)
    converged = False

    for iterations in range(1, max_iterations + 1):
        # Subdivide interval at midpoint
        mid = (low + high) / 2.0
        residual = f(mid)

        # Move left or right bound based on sign of f(mid)
        if residual < 0:
            low = mid
        else:
            high = mid

        # Check convergence using relative tolerance on interval width
        width = high - low
        scale = max(abs(mid), 1e-300)
        if abs(residual) < residual_tolerance or width / scale < relative_tolerance:
            converged = True
            break

    # Return final estimate as midpoint of [low, high]
    value = (low + high) / 2.0
    return SheetResistanceResult(
        value_ohm_per_sq=value,
        converged=converged,
        iterations=iterations,
        residual=f(value),
        r_a_ohm=r_a_ohm,
        r_b_ohm=r_b_ohm,
        r_a_over_r_b=r_a_ohm / r_b_ohm,
        approximation_ohm_per_sq=approximation,
    )


def reciprocity_error(forward_ohm: float, reciprocal_ohm: float) -> float:
    """Calculate reciprocity error |R(+I) - R(-I)| / mean(|R|).

    Measures how well the forward and reverse current resistances match.
    Ideally < 5% for good Ohmic behavior (symmetric contacts).
    High values indicate non-linear effects or asymmetric contacts.

    Args:
        forward_ohm: Resistance measured with positive current (+I).
        reciprocal_ohm: Resistance measured with negative current (-I).

    Returns:
        Reciprocity error as normalized difference [0, inf).

    Raises:
        ValueError: If both inputs are zero (denominator = 0).
    """
    denominator = mean([abs(forward_ohm), abs(reciprocal_ohm)])
    if denominator == 0:
        raise ValueError("reciprocity denominator is zero")
    return abs(forward_ohm - reciprocal_ohm) / denominator


def rotation_spread(resistances_ohm: list[float]) -> float:
    """Calculate anisotropy spread: std(R) / mean(|R|).

    For isotropic films, all contact pair resistance values should be similar.
    This metric quantifies the spread (anisotropy or non-uniformity).
    Ideally < 10% for uniform isotropic films.

    Args:
        resistances_ohm: List of resistance values to assess.

    Returns:
        Normalized standard deviation (rotation spread) [0, inf).

    Raises:
        ValueError: If list is empty or all values are zero.
    """
    if not resistances_ohm:
        raise ValueError("at least one resistance is required")
    denominator = mean(abs(value) for value in resistances_ohm)
    if denominator == 0:
        raise ValueError("rotation spread denominator is zero")
    return pstdev(resistances_ohm) / denominator


def vdp_asymmetry(r_a_ohm: float, r_b_ohm: float) -> float:
    """Calculate Van der Pauw asymmetry |Ra - Rb| / mean(|Ra|, |Rb|).

    Perfect Van der Pauw geometry has equal characteristic resistances (Ra = Rb).
    This metric quantifies deviation from ideal geometry.
    Values << 1 indicate good Van der Pauw symmetry.

    Args:
        r_a_ohm: Resistance from pair (A,B) measuring voltage at (C,D).
        r_b_ohm: Resistance from pair (C,D) measuring voltage at (A,B).

    Returns:
        Normalized asymmetry [0, inf).

    Raises:
        ValueError: If both inputs are zero.
    """
    denominator = mean([abs(r_a_ohm), abs(r_b_ohm)])
    if denominator == 0:
        raise ValueError("vdp asymmetry denominator is zero")
    return abs(r_a_ohm - r_b_ohm) / denominator


def _validate_positive_finite(name: str, value: float) -> None:
    if not isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be positive and finite")


def _ln2() -> float:
    # Kept local to avoid importing log solely for this constant.
    return 0.6931471805599453
