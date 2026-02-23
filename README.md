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
┌─────────────┐      ctypes/cdylib       ┌────────────────────┐
│ play_2048.py │  ◄─────────────────────► │ search2048 (Rust)  │
│  (Selenium)  │      search_ranked_moves │  expectimax engine │
└──────┬───────┘                          └────────────────────┘
       │  WebDriver
       ▼
┌──────────────┐
│   Chrome      │   Merge2048 (play2048.co)
│  (WebGL/PixiJS│   Svelte SPA
│   canvas)     │
└──────────────┘
```

| Component | Lines | Purpose |
|---|---|---|
| `play_2048.py` | ~1 100 | Browser automation, canvas reading, board sim, evaluation, power-ups, game loop |
| `search2048/src/lib.rs` | ~380 | Rust expectimax with transposition table, exported via C ABI |
| `run_debug.py` | 25 | Wrapper that tees stdout+stderr to `game_log.txt` |

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

---

## File Reference

| File | Description |
|---|---|
| `play_2048.py` | Main automation — everything in one file |
| `search2048/src/lib.rs` | Rust expectimax engine (cdylib) |
| `search2048/Cargo.toml` | Rust project configuration |
| `run_debug.py` | Logging wrapper (tees to `game_log.txt`) |
| `.gitignore` | Excludes build artifacts, logs, `__pycache__` |

---

## Lessons Learned

1. **PixiJS canvas games have no DOM to scrape** — pixel sampling is the only
   option without reverse-engineering the WASM/JS bundle.
2. **Colour matching is fragile** — position-dependent rendering shifts make
   fixed-threshold approaches fail for similar tile values.
3. **State tracking beats pixel reading** — computing tile values from game
   logic is more reliable than trying to distinguish visually-similar colours.
4. **Evaluation balance matters more than search depth** — the switch from
   linear to geometric snake weights was a bigger improvement than adding
   3 plies of search depth.
5. **Browser automation is a stability minefield** — focus loss, GPU crashes,
   memory leaks, overlay ads, and escape-key traps each required their own fix.
6. **Rust FFI via ctypes is straightforward** — a `cdylib` crate with
   `#[no_mangle] pub extern "C"` functions loads cleanly in Python.

---

## License

MIT
