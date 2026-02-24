# Merge2048 AI — Optimization Log

Each entry records an optimization iteration: what changed, why, and the result.

---

## Iteration 1: Initial Weight Tuning (Pre-Log)
**Commit**: Various prior commits through `910c659`
**Changes**: Hand-tuned evaluation weights (snake, corner, merge, scatter, etc.)
**Result**: First win at 2048 achieved once. Typical max: 512-1024.

---

## Iteration 2: Stronger Corner Discipline + Wall Bonus
**Commit**: (game25/26 iteration, pre-bitboard)
**Changes**:
- Corner bonus: 800 → 3000, edge penalty: -2000 → -6000, center: -5000 → -15000
- Added wall bonus: high tiles on edges get +200×lv², center tiles get -400×lv²
- Snake multiplier: 5.0 → 8.0
- Mono: 600 → 800, Smooth: 250 → 150, Merge: 1200 → 900
- Chain bonus: 500 → 1000, Adjacency: 400/800 → 800/1500
- Scatter: 3000/5000 → 5000/8000
- Empty scoring improved (0-merge: -1M, 1-no-merge: -50K)
- Depth +1 across all tiers (early game now 7-10)
**Result**: Game25: 256 (regression — too aggressive penalties?). Game26: 512 (two 512s couldn't merge).
**Analysis**: Weight tuning alone is insufficient. The cell-by-cell evaluation is too slow
for deep search. Need fundamental algorithmic change.

---

## Iteration 3: Bitboard Engine with Row Lookup Tables
**Commit**: TBD
**Changes**:
- Complete rewrite of Rust engine to use u64 bitboard (4 bits per tile = log2 value)
- Precomputed lookup tables (65536 entries each) for:
  - Row move results (left and right)
  - Merge scores per row
  - Heuristic evaluation per row
- Move simulation: 4 table lookups instead of cell-by-cell iteration
- Evaluation: 8 table lookups (4 rows + 4 columns via transpose)
- Transposition table keys: u64 bitboard IS its own hash (collision-free)
- Heuristic weights based on nneonneo/xificurk CMA-ES optimized values:
  - Empty: 270K per empty cell
  - Merges: 700K per merge group
  - Monotonicity: 47K × rank^4 penalty
  - Sum: 11K × rank^3.5 penalty (discourages tile accumulation)
- Added corner bonus as board-level feature on top of row heuristics
- Probability cutoff: skip chance branches below threshold
**Expected**: 100x+ faster search → can search depth 10-12 in same time as current depth 7-8
**Result**: TBD
