# auto2048 — AI that Wins Merge2048

An autonomous Python + Rust bot that opens a browser, plays
[Merge2048](https://play2048.co/) (the sliding-tile puzzle at play2048.co),
and reaches the 2048 tile.
It reads the board by sampling pixel colours from a **PixiJS WebGL canvas**,
runs expectimax search through a compiled **Rust DLL** for speed, and
strategically uses the game's three **power-up buttons** (undo, swap, delete)
to recover from bad positions.

---

## Quick Start

```
pip install selenium
cd search2048 && cargo build --release --target x86_64-pc-windows-gnu
cd ..
python run_debug.py          # logs to stdout + game_log.txt
```

Requirements: Python 3.10+, Selenium, Chrome + ChromeDriver, Rust toolchain
(with `x86_64-pc-windows-gnu` target), GCC linker (e.g. from Strawberry Perl
on Windows).

---

## Architecture

```
┌───────────────┐    ctypes/cdylib     ┌────────────────────┐
│  play_2048.py │ ◄──────────────────► │ search2048 (Rust)  │
│   (Selenium)  │  search_ranked_moves │  expectimax engine │
└───────┬───────┘                      └────────────────────┘
        │ WebDriver
        ▼
┌────────────────┐
│     Chrome     │  Merge2048 (play2048.co)
│  (WebGL/PixiJS │  Svelte SPA
│    canvas)     │
└────────────────┘
```

| Component | Lines | Purpose |
|---|---|---|
| `play_2048.py` | ~1 460 | Browser automation, canvas reading, board sim, power-ups, game loop |
| `search2048/src/lib.rs` | ~365 | Rust expectimax with bitboard engine, row lookup tables, C ABI |
| `run_debug.py` | 25 | Wrapper that tees stdout+stderr to `game_log.txt` |
| `OPTIMIZATION_LOG.md` | ~100 | Tracks each optimization iteration with changes, results |

---

## Development History

This project was built incrementally over multiple sessions, each discovering
new obstacles and devising solutions.  Below is a roughly chronological account
of every significant step.

### Phase 1 — First Contact (Sessions 1–2)

**Goal:** Open a browser and make any moves at all.

1. **Selenium scaffolding** — `pip install selenium`, Chrome WebDriver setup,
   open Merge2048 at `https://play2048.co/`.
2. **DOM tile reading failed** — the site is a modern Svelte SPA that renders
   the entire game board via **PixiJS on a WebGL `<canvas>`**.  There are *no*
   DOM elements for individual tiles; no `window.app` game-state object; no
   useful data in `localStorage`.  The game state is locked inside Svelte
   module closures and a Web Worker.
3. **Canvas pixel reading** — the breakthrough:  draw the WebGL canvas to an
   offscreen 2D canvas (`ctx.drawImage(webgl_canvas, 0, 0)`), then call
   `ctx.getImageData()` to sample RGB values at the 16 cell centres.
   Cell-centre proportional positions found by pixel-transition scanning:
   ```
   CX = [0.1867, 0.3950, 0.6033, 0.8117]
   CY = [0.1875, 0.3958, 0.6042, 0.8125]
   ```
4. **Colour-to-value mapping** — a `TILE_COLORS` dictionary maps each RGB
   triple to its tile value.  Initial palette came from the classic open-source
   2048 but had to be updated to match Merge2048's different colour scheme.
5. **Corner strategy** — a simple heuristic ("always press down, left, down,
   left …") got the bot playing its first 294 moves.

### Phase 2 — Expectimax AI (Session 2)

**Goal:** Replace the corner strategy with a proper search algorithm.

6. **Expectimax search** — max nodes try all four directions; chance nodes
   enumerate every empty cell and place a 2 (90 %) or 4 (10 %).
   `MAX_CHANCE_CELLS = 6` limits branching.
7. **Snake-weight evaluation** — tiles scored by position along a "snake" path
   that winds from one corner across the board.  Initial weights were powers of
   4 (`4^0 … 4^15`), which turned out to be far too steep—see Phase 4.
8. **Adaptive depth** — search depth increases as the board fills (more empty
   cells = wider tree but shallower needed, fewer = deeper search critical).

### Phase 3 — Power-Up Integration (Session 3)

**Goal:** Use the three buttons below the board: Undo, Swap, Delete.

9. **Button discovery** — buttons found relative to the canvas bottom edge via
   CSS selectors and bounding-box heuristics (`y >= canvas_bottom − 30`,
   `width ∈ [20, 120]`).
10. **Charge detection** — each button's parent has a `div.flex.gap-[3px]` with
    two child bars; solid `rgb(167,155,139)` = charged,
    `rgba(167,155,139,0.3)` = empty.  Up to 2 charges each.
11. **Undo** — click the first button to revert the last move.  Strategy:
    undo when the max tile is displaced from its corner or when the board
    collapses from ≥4 empty cells to ≤1.  Charges replenish at the 128-tile
    milestone.
12. **Swap** — click button, click first tile, click second tile.  Charges
    replenish at 256.  Initially disabled because canvas-cell clicking was
    unreliable; later fixed with `ActionChains` + JS-dispatch fallback.
13. **Delete** — click button, click a tile, all tiles of that value vanish.
    Charges replenish at 512.  Same click-reliability issue, fixed in Phase 5.

### Phase 4 — Stability Gauntlet (Sessions 3–4)

**Goal:** Stop the bot from freezing, crashing, or losing input focus.

14. **Focus loss** — pop-up overlays and ads stole keyboard focus.  Fix:
    switched from Selenium `ActionChains` key presses to
    `document.dispatchEvent(new KeyboardEvent(...))` in JavaScript—immune to
    focus state.
15. **Never press Escape** — discovered the hard way that `Escape` opens the
    game's pause menu, freezing play.  All code paths audited to remove it.
16. **`--disable-gpu` breaks WebGL** — this Chrome flag disables the GPU
    process, but Merge2048 *requires* WebGL for PixiJS rendering.  Removing
    it fixed canvas crashes.
17. **Memory leak** — creating a new offscreen `<canvas>` every board read
    leaked memory.  Fix: cache the canvas in `window._offCanvas`.
18. **Script timeout** — `driver.set_script_timeout(10)` + `try/except` around
    every Selenium call prevents indefinite hangs.
19. **Ad blocking** — iframes and high-z-index overlay divs removed every
    20 moves via `document.querySelectorAll('iframe').forEach(f => f.remove())`.
20. **Welcome-banner dismissal** — the site shows a "Play Tutorial" / X-close
    banner on first load.  Multiple strategies needed (the selector varies
    between visits): `button.rounded-full` width ≤ 40, button-text analysis,
    canvas-click fallback.  Clicking "Play Tutorial" locks the game into
    tutorial mode—avoid at all costs.

### Phase 5 — Rust Search Engine (Sessions 4–5)

**Goal:** Deeper search to break through the 512 plateau.

21. **Python bottleneck** — depth 5 in Python took 1–3 s per move with a tight
    board, limiting the AI to shallow strategies.
22. **Rust `cdylib`** — created `search2048/` crate, compiled with
    `x86_64-pc-windows-gnu` (MSVC was unavailable; GCC came from Strawberry
    Perl's toolchain).
23. **ctypes integration** — `search_ranked_moves(board, depth, scores, dirs)`
    exported via `#[no_mangle] pub extern "C"`.  Python loads the DLL at
    startup and falls back to the Python implementation if it's missing.
24. **Transposition table** — `HashMap<u64, (u32, f64)>` keyed on a 64-bit
    bitboard (4 bits per cell, log₂ values).  Cleared each top-level search,
    evicted at 2 M entries.
25. **8 snake orientations** — four row-wise and four column-wise snaking
    matrices; the best one is picked at evaluation time.
26. **Performance** — depth 7 in 200–400 ms; depth 8 in 35–800 ms; depth 9
    in 0.5–4 s.  Roughly **50–100×** faster than Python.
27. **Critical bug: linear weights in Rust** — the Rust engine was initially
    written with *linear* weights (1–16) while Python used geometric 1.5^n
    (1–438).  This meant the Rust AI was playing with a fundamentally worse
    evaluation.  Fixed by porting the geometric matrix verbatim.

### Phase 6 — The 256/512 Colour Crisis (Sessions 4–5)

**Goal:** Understand why the AI keeps dying with "two 512s" that won't merge.

28. **Symptom** — the game consistently reached 512 but then died with two
    adjacent tiles both reading as 512, which should merge but don't.
29. **Root cause** — Merge2048's gold-tile colours (128–2048) are very close
    in RGB space and **vary by board position** due to PixiJS rendering:
    | Tile | R | G | B (nominal) | B (observed range) |
    |------|---|---|-------------|-------------------|
    | 128  | 240 | 210 | 107 | 107 |
    | 256  | 242 | 210 | 96  | 96–97 |
    | 512  | 248 | 211 | 72  | 72–86 |
    | 1024 | 255 | 213 | 43  | 43 |

    The blue channel for 512 can shift from 72 up to 86 at certain positions—
    overlapping with 256's range.  No fixed threshold works.
30. **Evidence** — Game died with cells `(2,3)` at B=97 and `(3,3)` at B=86.
    If both were 256, a "down" move would merge them.  Manually verifying all
    possible moves on the board with `(3,3)=512` showed *no* valid moves—
    confirming the game was genuinely over and B=86 was 512, not 256.  A
    separate game showed B=84 was *actually* 256.  The same B-value range maps
    to *different* tile values in different games/positions.
31. **Blue-channel thresholds** — initial attempt: `B > 80 → 256`, else 512.
    Failed for B=86.  Adjusting to 90 would misclassify the B=84 case from
    the other game.  **Pixel reading alone cannot reliably distinguish 256
    from 512.**

### Phase 7 — State Tracking (Sessions 5–6, the winning fix)

**Goal:** Stop relying on pixel colours for gold tiles entirely.

32. **Insight** — the game starts with only 2/4 tiles (trivially identifiable).
    Every subsequent tile value is computed through merges.  If we track the
    board state through game logic, we never *need* to read gold-tile colours.
33. **`reconcile_board()`** — after each move, the bot computes the expected
    board via `simulate_move(tracked_board, direction)`, then reads the actual
    pixel board.  It trusts computation for gold tiles (128+) and trusts pixels
    only for newly-spawned 2/4 tiles and low-value cells (≤64).
34. **Move-didn't-register detection** — if the post-move pixel board is
    identical to the pre-move pixel board, the key press didn't reach the game.
    The bot retries once, then skips reconciliation to avoid corrupting state.
35. **Tracking resets** — after power-up use (undo/swap/delete), which change
    the board unpredictably, `tracked_board` is set to `None` and
    re-bootstrapped from the next pixel read.  A periodic reset every 50 moves
    acts as a safety net.
36. **Proactive power-ups** — after each move, the bot checks:
    - **Delete:** if ≤1 empty cell and max tile ≥128, delete the most common
      small tile to open space.
    - **Swap:** if the max tile has been displaced from its corner, swap it
      back.
    - All power-up button clicks use
      `driver.execute_script("arguments[0].click()", btn)` to bypass any
      overlay div that would block a normal Selenium click.
37. **Result** — with state tracking, the AI reliably passes through the
    256 → 512 → 1024 barrier that pixel-only reading could never cross.

### Phase 8 — Reaching 2048

38. **Adaptive depth with Rust** — depth 6 with ≥10 empty cells, 7 with ≥6,
    8 with ≥3, 9 with <3.
39. **Evaluation tuning** — snake weight multiplier reduced from 10 to 5 after
    switching to geometric weights (the weights themselves are ~27× steeper).
    Corner bonus/penalty also rebalanced.
40. **Game milestones** — 128 reached by move ~75; 256 by ~150; 512 by ~250;
    1024 by ~950; 2048 targeted by ~1500–2000.
41. **Power-up economy** — undo charges replenish at 128, swap at 256, delete
    at 512.  The bot uses them aggressively in the mid-game (plenty of
    replenishment) and conservatively late (charges are scarce).

### Phase 9 — Tracking Divergence & Stuck-Loop Fixes (Session 8)

**Goal:** Fix the bot's tendency to get stuck in infinite loops or
lose progress when tracking diverges from the actual game board.

42. **Infinite focus-recovery loop** — the bot's `same_count` was resetting
    to 5 at each focus retry, creating a cycle that never advanced to
    game-over detection or power-up use.  Fix: added a `focus_retries`
    counter (max 3) to prevent infinite retries.  (`846c275`)
43. **Tracking divergence detection** — after ~500 moves, the tracked board
    would silently diverge from the actual pixel board (usually due to a key
    dispatch that failed silently).  The stuck-recovery code would then try
    moves on the wrong board state, wasting power-ups and never matching.
    Fix: at `same_count == 5`, compare tracked vs pixel empty-cell positions;
    if symmetric-difference ≥ 1, reset tracking from pixels.  (`0b196e4`)
44. **Value-based divergence** — empty-cell comparison alone couldn't catch
    cases where both boards had the same number of empty cells but different
    values (e.g., tracked shows 1024 but pixels show 256).  Added cell-value
    mismatch counting (ignoring gold 2x misreads); divergence triggers at
    ≥ 4 value mismatches.  (`c22dab1`)
45. **ActionChains key fallback** — when JS `dispatchEvent` stops working
    (after ~1000 moves), the bot now tries Selenium ActionChains
    (click canvas + `send_keys`) as a fallback.  Added `send_key_fallback()`
    used both in the main move loop and in focus-retry recovery.  (`112cafa`)
46. **Board-change verification after power-ups** — DELETE/UNDO button clicks
    could "succeed" (Selenium clicks the button) but not actually change
    the board.  The bot would reset `same_count` and loop forever.  Fix:
    re-read the pixel board after every power-up use and only reset
    `same_count` if the board actually changed.  (`eec4f1c`)

### Phase 10 — Gold Tile Preservation (Session 8)

**Goal:** Stop losing high tiles (1024→512→256) during divergence recovery.

47. **Reconcile during divergence reset** — when tracking divergence was
    detected, the old code replaced `tracked_board` with raw `pixel_board`,
    which misread gold tiles (e.g., 1024 shows as 256 in pixels).  Fix:
    use `reconcile_board()` to merge tracked and pixel data, preserving
    gold tile values from tracking.  (`e1a6534`)
48. **Multi-level reconcile** — `reconcile_board()` originally only handled
    2x misreads (512→256).  But gold tiles can be misread at 4x or even 8x
    (1024→256).  Extended cell-by-cell comparison to handle 4x misreads,
    and the count-based upgrade to try divisors of 2, 4, and 8.  (`19886a3`)
49. **4x gold misread tolerance in divergence detection** — the divergence
    check was counting cells as "mismatched" if values differed by more
    than 2x.  Added `_is_gold_misread()` helper that tolerates 2x and 4x
    differences between tracked and pixel values.  (`2049a78`)

### Phase 11 — Key Dispatch Recovery & Evaluation Tuning (Session 8)

**Goal:** Handle the persistent key dispatch failure that kills games
after ~1000 moves, and improve merge strategy.

50. **Page refresh recovery** — after all other stuck-recovery mechanisms
    fail (direction probing, power-ups, ActionChains, focus retry ×3), the
    bot now refreshes the page (up to 2 times) to reset JavaScript event
    handlers.  Game state is preserved via localStorage.  After refresh,
    `reconcile_board()` restores gold tile values.  (`6d62303`)
51. **Prevent recovery fallthrough** — when stuck-recovery tries all 4
    directions and none work, it now `continue`s to the next loop iteration
    instead of falling through to the regular search+move code (which also
    fails, wasting time and incrementing the move counter).  (`6d62303`)
52. **Strategic merge bonus** — adjacent tiles equal to the max tile value
    get a 5× merge bonus (e.g., two 512s → one 1024).  Tiles half the max
    get a 3× bonus.  This addresses game20 where two adjacent 512s weren't
    merged.  Merge coefficient raised from 800 to 1200.  (`5c8e5f1`)
53. **Stronger corner discipline** — corner bonus increased (500→800 per
    lv²), edge penalty doubled (-1000→-2000), center penalty increased
    (-3000→-5000).  Addresses game21 where 1024 ended up at an edge
    position instead of corner.  (`e5aec23`)
54. **Stronger scatter penalty** — base penalty for non-adjacent duplicate
    high tiles increased from 2000→3000, and from 4000→5000 for
    half-max duplicates.  (`e5aec23`)

### Phase 12 — Bitboard Engine & Consistent Wins (Session 9)

**Goal:** Rewrite the search engine for dramatically faster, deeper search.

55. **Bitboard architecture** — complete rewrite of `lib.rs` to use a `u64`
    bitboard (4 bits per tile = log₂ value).  Precomputed 65 536-entry lookup
    tables for move simulation, merge scoring, and heuristic evaluation.
    Move = 4 table lookups; evaluation = 8 lookups (4 rows + 4 columns via
    bit-parallel transpose).  Based on [nneonneo/2048-ai](https://github.com/nneonneo/2048-ai).
56. **Critical merge-counting bug** — the heuristic table builder used
    `prev != 0` to check for merge groups, causing rows like `[1,2,3,4]`
    (all different) to score `merges=4`.  Fixed to `counter > 0` — only
    consecutive equal tiles count as merges.  This was *the* primary bug
    preventing reliable wins.
57. **Weight scale fix** — heuristic weights were initially 1000× too large
    (270K instead of 270).  The `W_LOST` constant (200 000) is the baseline
    per row; feature weights are small adjustments around it.
58. **Heuristic sign fix** — `score_move_node` initialises `best = 0.0`.
    With the original negative heuristic (`−W_LOST`), game-over (0) looked
    *better* than playing.  Changing to `+W_LOST` makes all scores positive.
59. **Corner bonus removed** — the board-level corner bonus overlay conflicted
    with the row-based monotonicity heuristic.  nneonneo achieves 100% win
    rate without any corner bonus; the monotonicity penalty alone naturally
    pushes high tiles to corners.
60. **Bit-parallel transpose** — replaced the 16-iteration loop with nneonneo's
    two-round bit manipulation transpose (6 bitwise ops + 4 shifts).
61. **Deeper late-game search** — depth increased to 7–9 for `mt ≥ 1024`
    (was 6–8), allowing the engine to plan further ahead during the critical
    1024 → 2048 transition.
62. **Result** — four consecutive wins (Games 31–34), with one game reaching
    4096 post-win.  Engine confirmed as **consistently winning**.

### Current Status

The bot **consistently wins** (reaches 2048) — four consecutive wins confirmed
(Games 31–34).  In one game it continued to **4096** post-win.  Typical
win occurs around move 950–1000.

| Game | Result | Moves | Notes |
|------|--------|-------|-------|
| 31 | **WIN 2048** | 1000 | First win with bitboard engine |
| 32 | **WIN 2048** → 4096 | 998 | Reached 4096 after continuing |
| 33 | **WIN 2048** | 979 | Perfect snake pattern |
| 34 | **WIN 2048** | 997 | 1024→512 in column 0 |

Remaining minor issues:
- **Post-win key dispatch** — after ~1000 moves, JS `dispatchEvent` stops
  working, causing DELETE spam.  Not critical since the game is already won.
- **Gold tile colour ambiguity** — handled by state tracking + reconcile.

---

## Key Technical Decisions

### Why pixel reading instead of DOM/API?

Merge2048 (play2048.co) uses PixiJS on a WebGL canvas with Svelte.  There are *no* DOM
nodes for tiles, no accessible `window.game` object, and the game state lives
inside Svelte closures and a Web Worker.  Canvas pixel sampling was the only
reliable way to read the board.

### Why Rust?

Python expectimax at depth 5 takes 1–3 s per move.  The Rust engine handles
depth 8 in under a second—roughly 50–100× faster—enabling the AI to plan
far enough ahead to set up high-value merges.

### Why state tracking?

PixiJS renders the same tile value with slightly different colours depending
on board position, animation state, or rendering order.  Tiles 256 and 512
have overlapping blue-channel ranges (B ∈ [84, 97]), making reliable pixel
identification impossible.  Tracking the board through game logic and only
reading pixel colours for newly-spawned 2/4 tiles (which are trivially
distinguishable) sidesteps the problem entirely.

---

## Stability Rules (Hard-Won)

These were each discovered through a crash, freeze, or infinite loop:

| Rule | Why |
|---|---|
| Use JS `dispatchEvent(new KeyboardEvent(...))` for moves | Immune to focus loss from pop-ups |
| **Never** send the Escape key | Opens the game's pause menu |
| Do **not** pass `--disable-gpu` to Chrome | Breaks WebGL; PixiJS needs GPU |
| Cache the offscreen canvas in `window._offCanvas` | Prevents memory leak |
| Set `driver.set_script_timeout(10)` | Prevents indefinite Selenium hangs |
| Wrap every Selenium call in `try/except` | Browser can disconnect at any time |
| Click power-up buttons via `execute_script("arguments[0].click()")` | Overlay divs block normal clicks |
| Dismiss the welcome banner's **X button**, never "Play Tutorial" | Tutorial mode locks out arrow keys |
| Save `prev_tracked_ref` before any `tracked_board = None` | Enables reconcile after tracking reset |
| Divergence reset must use `reconcile_board()` | Raw pixel replacement loses gold tile values |
| Verify board changed after DELETE/UNDO | Button click can succeed without effect |
| Page refresh as last resort (max 2×) | Resets dead JS event handlers |

---

## Evaluation Function (Rust — Bitboard Engine)

The engine was rewritten in Iteration 3–5 to use a **bitboard architecture**
inspired by [nneonneo/2048-ai](https://github.com/nneonneo/2048-ai).

### Bitboard Representation

The board is packed into a single `u64`: 16 nybbles, each storing the log₂
of the tile value (0 = empty, 1 = 2, 2 = 4, …, 11 = 2048).

```
Board = u64:  row0[0:15]  row1[16:31]  row2[32:47]  row3[48:63]
Within row:   col0[0:3]   col1[4:7]    col2[8:11]   col3[12:15]
```

### Precomputed Lookup Tables

Four 65 536-entry tables are built once at startup (covering all possible
16-bit row values):

| Table | Purpose |
|-------|---------|
| `TBL_LEFT[row]` | Result row after left-move (slide + merge) |
| `TBL_RIGHT[row]` | Result row after right-move |
| `TBL_SCORE[row]` | Merge score (actual game points) for left-move |
| `TBL_HEUR[row]` | Heuristic evaluation per row |

Move simulation = 4 table lookups.  Up/down: transpose → left/right → transpose back.
Evaluation = 8 table lookups (4 rows + 4 columns via bit-parallel transpose).

### Heuristic Weights (CMA-ES optimized, nneonneo/xificurk)

| Component | Weight | Description |
|-----------|--------|-------------|
| **Lost baseline** | +200 000 per row | Ensures all playable states score > 0 (game-over = 0) |
| **Empty cells** | +270 per empty | Rewards open space |
| **Merge groups** | +700 per group | Consecutive equal tiles that can merge |
| **Monotonicity** | −47 × Δrank⁴ | Penalises non-monotonic sequences within a row |
| **Sum** | −11 × rank³·⁵ | Discourages excessive tile accumulation |

### Search Architecture

- **Expectimax** with separate chance/move node functions
- Depth counts **move nodes only** (not alternating max/chance)
- **Probability pruning**: branches with cumulative probability < 0.0001 are pruned
- **Adaptive depth**: `max(python_depth, distinct_tiles − 2)`
- **Transposition table**: `HashMap<u64, (depth, score)>`, cleared per top-level search
- Typical performance: depth 7–9 in 50–1000 ms

---

## File Reference

| File | Description |
|---|---|
| `play_2048.py` | Main automation — everything in one file |
| `search2048/src/lib.rs` | Rust bitboard expectimax engine (cdylib) |
| `search2048/Cargo.toml` | Rust project configuration |
| `run_debug.py` | Logging wrapper (tees to `game_log.txt`) |
| `OPTIMIZATION_LOG.md` | Optimization iteration history |
| `.gitignore` | Excludes build artifacts, logs, `__pycache__` |

---

## Lessons Learned

1. **PixiJS canvas games have no DOM to scrape** — pixel sampling is the only
   option without reverse-engineering the WASM/JS bundle.
2. **Colour matching is fragile** — position-dependent rendering shifts make
   fixed-threshold approaches fail for similar tile values.
3. **State tracking beats pixel reading** — computing tile values from game
   logic is more reliable than trying to distinguish visually-similar colours.
4. **Bitboard + lookup tables are transformative** — switching from cell-by-cell
   evaluation to precomputed row tables gave ~50× speedup, enabling depth 9
   search in under a second.
5. **Small heuristic bugs have catastrophic effects** — a single wrong
   comparison (`prev != 0` vs `counter > 0`) in merge counting made the
   engine think dead boards had merge potential.  The sign of the lost
   penalty (`-W_LOST` vs `+W_LOST`) made game-over look better than playing.
6. **Less is more in evaluation** — removing the hand-crafted corner bonus
   and using the pure CMA-ES-optimized row heuristic improved results.  The
   monotonicity penalty naturally achieves corner discipline.
7. **Browser automation is a stability minefield** — focus loss, GPU crashes,
   memory leaks, overlay ads, and escape-key traps each required their own fix.
8. **Rust FFI via ctypes is straightforward** — a `cdylib` crate with
   `#[no_mangle] pub extern "C"` functions loads cleanly in Python.
9. **Divergence between tracking and reality is insidious** — a single missed
   key dispatch silently corrupts the tracked state; multi-layered detection
   is needed to catch and correct it.
10. **Study reference implementations** — reading nneonneo's actual source code
    revealed three critical bugs in our engine that would have been nearly
    impossible to find through testing alone.

---

## License

MIT
