"""
Merge2048 Game Automation — Winning AI with power-up support.

Reads the board by sampling tile colours from the PixiJS canvas, uses
expectimax search (with chance nodes) and a balanced evaluation function,
and leverages the game's three power-ups (undo, swap, delete) when in
trouble to push past difficult positions.

Game: https://play2048.co/
"""

import os
import time
import math
import ctypes
from collections import Counter
from selenium import webdriver
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains

# ── Rust search engine (optional, much faster) ────────────────────
_RUST_DLL = None
_RUST_DIR_MAP = {0: "up", 1: "down", 2: "left", 3: "right"}
_dll_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
    "search2048", "target", "x86_64-pc-windows-gnu", "release", "search2048.dll")
if os.path.exists(_dll_path):
    try:
        _RUST_DLL = ctypes.CDLL(_dll_path)
        _RUST_DLL.search_ranked_moves.restype = ctypes.c_uint32
        _RUST_DLL.search_ranked_moves.argtypes = [
            ctypes.POINTER(ctypes.c_uint16), ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_double), ctypes.POINTER(ctypes.c_uint8)]
        print(f"✓ Rust search engine loaded")
    except Exception as e:
        print(f"⚠ Rust DLL load failed: {e}, using Python fallback")
        _RUST_DLL = None
else:
    print("⚠ Rust DLL not found, using Python fallback")

URL = "https://play2048.co/"
MOVE_DELAY = 0.45

KEY_MAP = {
    "up": Keys.ARROW_UP,
    "down": Keys.ARROW_DOWN,
    "left": Keys.ARROW_LEFT,
    "right": Keys.ARROW_RIGHT,
}
DIRECTIONS = ["up", "down", "left", "right"]

# ── Tile colour palette (confirmed on play2048.co) ────────────────
TILE_COLORS = {
    (238, 228, 218): 2,     # #eee4da — verified exact
    (235, 216, 182): 4,     # #ebd8b6 — actual game color
    (242, 177, 120): 8,     # #f2b178 — verified
    (246, 148, 97):  16,    # #f69461 — verified
    (247, 127, 99):  32,    # #f77f63 — verified
    (247, 100, 67):  64,    # #f76443 — verified
    (240, 210, 107): 128,   # #f0d26b — verified
    (242, 210, 96):  256,   # #f2d260 — verified
    (248, 211, 72):  512,   # #f8d348 — verified
    (240, 195, 48):  1024,  # extrapolated from pattern
    (237, 190, 36):  2048,  # extrapolated from pattern
    (60,  58,  50):  4096,
}

# ── Snake weights ─────────────────────────────────────────────────
# Geometric series (base ≈ 1.5) along the snake path, 4 orientations.
WEIGHT_MATRICES = [
    [[438, 292, 195, 130],
     [26,  38,  58,  87],
     [17,  11,  8,   5],
     [1,   2,   2,   3]],
    [[130, 195, 292, 438],
     [87,  58,  38,  26],
     [5,   8,   11,  17],
     [3,   2,   2,   1]],
    [[1,   2,   2,   3],
     [17,  11,  8,   5],
     [26,  38,  58,  87],
     [438, 292, 195, 130]],
    [[3,   2,   2,   1],
     [5,   8,   11,  17],
     [87,  58,  38,  26],
     [130, 195, 292, 438]],
]

LOG2 = {0: 0}
for _i in range(1, 18):
    LOG2[1 << _i] = _i

# Cell-centre proportional positions on the canvas
CX = [0.1867, 0.3950, 0.6033, 0.8117]
CY = [0.1875, 0.3958, 0.6042, 0.8125]

# ── JavaScript snippets ──────────────────────────────────────────
# Sample at 3 points per cell (UL, UR, LL quadrants), take mode for robustness
JS_READ_BOARD = """
var c = document.querySelector('canvas');
if (!c) return null;
if (!window._offCanvas) {
    window._offCanvas = document.createElement('canvas');
    window._offCtx = window._offCanvas.getContext('2d');
}
var o = window._offCanvas, ctx = window._offCtx;
o.width = c.width; o.height = c.height;
ctx.drawImage(c, 0, 0);
var w = c.width, h = c.height;
var cx = [0.1867, 0.3950, 0.6033, 0.8117];
var cy = [0.1875, 0.3958, 0.6042, 0.8125];
var cellW = 0.208 * w, cellH = 0.208 * h;
var out = [];
for (var r = 0; r < 4; r++)
    for (var col = 0; col < 4; col++) {
        var centerX = Math.round(cx[col] * w);
        var centerY = Math.round(cy[r] * h);
        var offsets = [
            [-0.30 * cellW, -0.30 * cellH],
            [ 0.30 * cellW, -0.30 * cellH],
            [-0.30 * cellW,  0.30 * cellH]
        ];
        var votes = {};
        for (var k = 0; k < offsets.length; k++) {
            var px = Math.round(centerX + offsets[k][0]);
            var py = Math.round(centerY + offsets[k][1]);
            var d = ctx.getImageData(px, py, 1, 1).data;
            var key = (d[0] << 16) | (d[1] << 8) | d[2];
            votes[key] = (votes[key] || 0) + 1;
        }
        var bestKey = 0, bestCount = 0;
        for (var key in votes) {
            if (votes[key] > bestCount) {
                bestCount = votes[key];
                bestKey = parseInt(key);
            }
        }
        out.push(bestKey);
    }
return out;
"""

# Returns [undo_charges, swap_charges, delete_charges]
JS_GET_CHARGES = """
var canvas = document.querySelector('canvas');
if (!canvas) return [];
var cb = canvas.getBoundingClientRect().bottom;
var buttons = document.querySelectorAll('button');
var result = [];
for (var i = 0; i < buttons.length; i++) {
    var r = buttons[i].getBoundingClientRect();
    if (r.y >= cb - 30 && r.y <= cb + 200
        && r.width > 20 && r.width < 120
        && r.height > 20 && r.height < 120) {
        var parent = buttons[i].parentElement;
        var charges = 0;
        for (var j = 0; j < parent.children.length; j++) {
            var cls = parent.children[j].className || '';
            if (cls.indexOf('gap-') >= 0) {
                for (var k = 0; k < parent.children[j].children.length; k++) {
                    var bg = window.getComputedStyle(
                        parent.children[j].children[k]).backgroundColor;
                    if (bg.indexOf('0.3') < 0 && bg.indexOf('rgba') < 0)
                        charges++;
                }
            }
        }
        result.push(charges);
    }
}
return result;
"""


# ── Colour → value ────────────────────────────────────────────────
# Background color for empty cells
EMPTY_COLOR = (189, 172, 151)

def packed_to_value(packed):
    r = (packed >> 16) & 0xFF
    g = (packed >> 8) & 0xFF
    b = packed & 0xFF
    # Check empty first
    if abs(r - EMPTY_COLOR[0]) + abs(g - EMPTY_COLOR[1]) + abs(b - EMPTY_COLOR[2]) < 30:
        return 0
    # Gold tile zone (128-2048): R>230, G in 185-220, B<120
    # These tiles are very close in RGB; use blue channel as discriminator
    if r > 230 and 185 < g < 220 and b < 120:
        if b > 100:   return 128   # B≈107
        elif b > 80:  return 256   # B≈96
        elif b > 58:  return 512   # B≈72
        elif b > 40:  return 1024  # B≈48 (extrapolated)
        else:          return 2048  # B≈36 (extrapolated)
    # Non-gold tiles: standard distance matching
    best_val, best_dist = 0, 999
    for (tr, tg, tb), val in TILE_COLORS.items():
        if val >= 128:
            continue
        dist = abs(r - tr) + abs(g - tg) + abs(b - tb)
        if dist < best_dist:
            best_dist = dist
            best_val = val
    return best_val if best_dist < 50 else 0


# ── Board simulation ──────────────────────────────────────────────

def compress_line(line):
    tiles = [v for v in line if v != 0]
    merged, score, i = [], 0, 0
    while i < len(tiles):
        if i + 1 < len(tiles) and tiles[i] == tiles[i + 1]:
            merged.append(tiles[i] * 2)
            score += tiles[i] * 2
            i += 2
        else:
            merged.append(tiles[i])
            i += 1
    merged += [0] * (4 - len(merged))
    return merged, score


def simulate_move(board, direction):
    nb = [row[:] for row in board]
    total, moved = 0, False
    for i in range(4):
        if direction == "left":
            line = nb[i][:]
            res, sc = compress_line(line)
            if res != nb[i]: moved = True
            nb[i] = res
        elif direction == "right":
            line = nb[i][::-1]
            res, sc = compress_line(line)
            res = res[::-1]
            if res != nb[i]: moved = True
            nb[i] = res
        elif direction == "up":
            line = [nb[r][i] for r in range(4)]
            res, sc = compress_line(line)
            if res != line: moved = True
            for r in range(4): nb[r][i] = res[r]
        elif direction == "down":
            line = [nb[r][i] for r in range(3, -1, -1)]
            res, sc = compress_line(line)
            res2 = res[::-1]
            if res2 != [nb[r][i] for r in range(4)]: moved = True
            for r in range(4): nb[r][i] = res2[r]
        total += sc
    return nb, total, moved


# ── Evaluation ─────────────────────────────────────────────────────

def evaluate(board):
    snake = max(
        sum(board[r][c] * w[r][c] for r in range(4) for c in range(4))
        for w in WEIGHT_MATRICES
    )

    empty = sum(1 for r in range(4) for c in range(4) if board[r][c] == 0)
    if empty == 0:
        empty_score = -200000
    elif empty <= 2:
        empty_score = empty * 5000
    else:
        empty_score = 3000 * empty + 5000 * math.log2(empty)

    mt = max(board[r][c] for r in range(4) for c in range(4))
    corners = [board[0][0], board[0][3], board[3][0], board[3][3]]
    if mt in corners:
        corner_score = mt * 5
    else:
        on_edge = (
            mt in [board[0][c] for c in range(4)] or
            mt in [board[3][c] for c in range(4)] or
            mt in [board[r][0] for r in range(4)] or
            mt in [board[r][3] for r in range(4)]
        )
        corner_score = -mt * 3 if on_edge else -mt * 10

    smooth = 0
    for r in range(4):
        for c in range(3):
            v1, v2 = board[r][c], board[r][c + 1]
            if v1 and v2:
                smooth -= abs(LOG2.get(v1, 0) - LOG2.get(v2, 0))
    for c in range(4):
        for r in range(3):
            v1, v2 = board[r][c], board[r + 1][c]
            if v1 and v2:
                smooth -= abs(LOG2.get(v1, 0) - LOG2.get(v2, 0))

    mono = 0
    for r in range(4):
        row = [board[r][c] for c in range(4)]
        left = sum(1 for i in range(3) if row[i] >= row[i + 1])
        right = sum(1 for i in range(3) if row[i] <= row[i + 1])
        mono += max(left, right)
    for c in range(4):
        col = [board[r][c] for r in range(4)]
        up = sum(1 for i in range(3) if col[i] >= col[i + 1])
        down = sum(1 for i in range(3) if col[i] <= col[i + 1])
        mono += max(up, down)

    merges = 0
    for r in range(4):
        for c in range(4):
            v = board[r][c]
            if v == 0:
                continue
            if c + 1 < 4 and board[r][c + 1] == v:
                merges += LOG2.get(v, 0)
            if r + 1 < 4 and board[r + 1][c] == v:
                merges += LOG2.get(v, 0)

    return snake + empty_score + corner_score + smooth * 100 + mono * 200 + merges * 500


# ── Expectimax search ─────────────────────────────────────────────

MAX_CHANCE_CELLS = 6


def expectimax(board, depth, is_max_node):
    if depth == 0:
        return evaluate(board)
    if is_max_node:
        best = -float("inf")
        for d in DIRECTIONS:
            nb, ms, moved = simulate_move(board, d)
            if not moved:
                continue
            best = max(best, expectimax(nb, depth - 1, False) + ms)
        return best if best != -float("inf") else evaluate(board)
    else:
        empty = [(r, c) for r in range(4) for c in range(4) if board[r][c] == 0]
        if not empty:
            return evaluate(board)
        if len(empty) > MAX_CHANCE_CELLS:
            scored = []
            for r, c in empty:
                adj = sum(
                    1 for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]
                    if 0 <= r + dr < 4 and 0 <= c + dc < 4
                    and board[r + dr][c + dc] > 0
                )
                scored.append((-adj, r, c))
            scored.sort()
            empty = [(r, c) for _, r, c in scored[:MAX_CHANCE_CELLS]]
        total = 0.0
        for r, c in empty:
            for val, prob in [(2, 0.9), (4, 0.1)]:
                nb = [row[:] for row in board]
                nb[r][c] = val
                total += prob * expectimax(nb, depth - 1, True)
        return total / len(empty)


def ranked_moves(board, depth=4):
    """Return list of (score, direction, simulated_board) sorted best first."""
    if _RUST_DLL is not None:
        flat = (ctypes.c_uint16 * 16)(*[board[r][c] for r in range(4) for c in range(4)])
        scores = (ctypes.c_double * 4)()
        dirs = (ctypes.c_uint8 * 4)()
        n = _RUST_DLL.search_ranked_moves(flat, depth, scores, dirs)
        moves = []
        for i in range(n):
            d_name = _RUST_DIR_MAP[dirs[i]]
            nb, ms, moved = simulate_move(board, d_name)
            moves.append((scores[i], d_name, nb))
        return moves
    # Python fallback
    moves = []
    for d in DIRECTIONS:
        nb, ms, moved = simulate_move(board, d)
        if not moved:
            continue
        score = expectimax(nb, depth, False) + ms
        moves.append((score, d, nb))
    moves.sort(reverse=True)
    return moves


# ── Browser helpers ────────────────────────────────────────────────

def read_board(driver):
    try:
        colors = driver.execute_script(JS_READ_BOARD)
    except Exception:
        return None
    if not colors or len(colors) != 16:
        return None
    board = [[0] * 4 for _ in range(4)]
    for i, packed in enumerate(colors):
        r, c = divmod(i, 4)
        board[r][c] = packed_to_value(packed)
    return board


JS_KEY_CODES = {
    "up": ("ArrowUp", 38),
    "down": ("ArrowDown", 40),
    "left": ("ArrowLeft", 37),
    "right": ("ArrowRight", 39),
}


def send_key(driver, direction):
    key, code = JS_KEY_CODES[direction]
    try:
        driver.execute_script("""
        var opts = {key: arguments[0], code: arguments[0], keyCode: arguments[1],
                    which: arguments[1], bubbles: true, cancelable: true};
        document.dispatchEvent(new KeyboardEvent('keydown', opts));
        document.dispatchEvent(new KeyboardEvent('keyup', opts));
        """, key, code)
    except Exception:
        pass


def max_tile(board):
    return max(board[r][c] for r in range(4) for c in range(4))


def empty_count(board):
    return sum(1 for r in range(4) for c in range(4) if board[r][c] == 0)


def print_board(board, label=""):
    if label:
        print(f"\n── {label} ──")
    for row in board:
        print("  ".join(str(v).rjust(5) if v else "    ." for v in row))
    print()


def corner_has_max(board):
    mt = max_tile(board)
    return mt in (board[0][0], board[0][3], board[3][0], board[3][3])


# ── Power-up helpers ──────────────────────────────────────────────
# play2048.co has 3 power-ups below the board:
#   0 = Undo         (starts with 2 charges, earn more at 128 tile)
#   1 = Swap tiles   (starts with 1 charge,  earn more at 256 tile)
#   2 = Delete by #  (starts with 0 charges, earn more at 512 tile)

def get_power_buttons(driver):
    """Return list of the 3 power-up button WebElements."""
    try:
        return driver.execute_script("""
        var canvas = document.querySelector('canvas');
        if (!canvas) return [];
        var cb = canvas.getBoundingClientRect().bottom;
        var buttons = document.querySelectorAll('button');
        var g = [];
        for (var i = 0; i < buttons.length; i++) {
            var r = buttons[i].getBoundingClientRect();
            if (r.y >= cb - 30 && r.y <= cb + 200
                && r.width > 20 && r.width < 120
                && r.height > 20 && r.height < 120)
                g.push(buttons[i]);
        }
        return g;
        """)
    except Exception:
        return []


def get_charges(driver):
    """Return [undo, swap, delete] charge counts."""
    try:
        result = driver.execute_script(JS_GET_CHARGES)
        if result and len(result) == 3:
            return result
    except Exception:
        pass
    return [0, 0, 0]


def use_undo(driver):
    """Click the undo button. Returns True if successful."""
    try:
        btns = get_power_buttons(driver)
        if not btns:
            return False
        driver.execute_script("arguments[0].click()", btns[0])
        time.sleep(MOVE_DELAY + 0.15)
        return True
    except Exception:
        return False


def click_canvas_cell(driver, row, col):
    """Click on a specific cell of the game canvas using ActionChains."""
    try:
        canvas = driver.find_element(By.CSS_SELECTOR, "canvas")
        rect = driver.execute_script(
            "var r=arguments[0].getBoundingClientRect();"
            "return {w:r.width, h:r.height}", canvas)
        # Offset from element center
        x = CX[col] * rect['w'] - rect['w'] / 2
        y = CY[row] * rect['h'] - rect['h'] / 2
        ActionChains(driver).move_to_element_with_offset(
            canvas, int(x), int(y)).click().perform()
    except Exception:
        # Fallback to JS dispatch
        driver.execute_script("""
        var c = document.querySelector('canvas');
        var r = c.getBoundingClientRect();
        var cx = [0.1867, 0.3950, 0.6033, 0.8117];
        var cy = [0.1875, 0.3958, 0.6042, 0.8125];
        var x = r.left + cx[arguments[1]] * r.width;
        var y = r.top + cy[arguments[0]] * r.height;
        var opts = {clientX: x, clientY: y, bubbles: true, cancelable: true};
        c.dispatchEvent(new PointerEvent('pointerdown', opts));
        c.dispatchEvent(new PointerEvent('pointerup', opts));
        c.dispatchEvent(new MouseEvent('click', opts));
        """, row, col)


def cancel_power_mode(driver):
    """Cancel any active power-up selection mode by clicking body."""
    try:
        driver.execute_script(
            "document.body.click(); "
            "var c = document.querySelector('canvas'); if(c) c.click();")
    except Exception:
        pass
    time.sleep(0.3)


def use_swap(driver, r1, c1, r2, c2):
    """Activate swap, click first tile then second tile."""
    try:
        board_before = read_board(driver)
        btns = get_power_buttons(driver)
        if not btns or len(btns) < 2:
            return False
        driver.execute_script("arguments[0].click()", btns[1])
        time.sleep(1.0)
        click_canvas_cell(driver, r1, c1)
        time.sleep(1.0)
        click_canvas_cell(driver, r2, c2)
        time.sleep(1.0)
        board_after = read_board(driver)
        if board_after == board_before:
            cancel_power_mode(driver)
            return False
        cancel_power_mode(driver)
        return True
    except Exception as e:
        print(f"  ⚠ Swap failed: {e}")
        cancel_power_mode(driver)
        return False


def use_delete(driver, row, col):
    """Activate delete, click the tile whose value to remove."""
    try:
        board_before = read_board(driver)
        btns = get_power_buttons(driver)
        if not btns or len(btns) < 3:
            return False
        driver.execute_script("arguments[0].click()", btns[2])
        time.sleep(1.0)
        click_canvas_cell(driver, row, col)
        time.sleep(1.0)
        board_after = read_board(driver)
        if board_after == board_before:
            cancel_power_mode(driver)
            return False
        cancel_power_mode(driver)
        return True
    except Exception as e:
        print(f"  ⚠ Delete failed: {e}")
        cancel_power_mode(driver)
        return False


# ── Power-up strategy ────────────────────────────────────────────

def should_undo(board_before, board_after, moves_ranked):
    """Decide whether to undo the last move.

    Strategy: undo when the ACTUAL board after the move is substantially worse
    than what the AI expected, OR when the move caused a clear structural
    problem (max tile displaced from corner).  We compare the real post-move
    evaluation against the second-best move's predicted score — if the
    alternative is clearly better, undoing is worthwhile.

    Charges replenish when reaching tile milestones (128 → +1 undo,
    256 → +1 swap, 512 → +1 delete), so early use is acceptable as long
    as it meaningfully improves the board.
    """
    if board_before is None or board_after is None:
        return False
    if len(moves_ranked) < 2:
        return False  # no alternative to try

    mt = max_tile(board_before)
    eval_after = evaluate(board_after)
    _, _, alt_board = moves_ranked[1]
    eval_alt = evaluate(alt_board)

    # 1. Max tile left corner → undo if max tile is significant
    if mt >= 64 and corner_has_max(board_before) and \
       not corner_has_max(board_after):
        return True

    # 2. Score-drop check: the actual result is much worse than the
    #    alternative move's *predicted* board (before random tile).
    #    Use a relative threshold that scales with board complexity.
    if mt >= 64:
        drop = eval_alt - eval_after
        threshold = max(abs(eval_alt) * 0.15, 5000)
        if drop > threshold:
            return True

    # 3. Emergency: went from safe (4+ empty) to near-death (≤1 empty)
    if empty_count(board_before) >= 4 and empty_count(board_after) <= 1:
        return True

    return False


def find_best_swap(board):
    """If a single swap would noticeably improve the board, return (r1,c1,r2,c2)."""
    base = evaluate(board)
    best_gain, best_swap = 0, None
    cells = [(r, c) for r in range(4) for c in range(4) if board[r][c] > 0]
    for i in range(len(cells)):
        for j in range(i + 1, len(cells)):
            r1, c1 = cells[i]
            r2, c2 = cells[j]
            if board[r1][c1] == board[r2][c2]:
                continue
            nb = [row[:] for row in board]
            nb[r1][c1], nb[r2][c2] = nb[r2][c2], nb[r1][c1]
            gain = evaluate(nb) - base
            if gain > best_gain:
                best_gain, best_swap = gain, (r1, c1, r2, c2)
    # Only swap if gain is significant (> 10% of current eval)
    if best_swap and best_gain > abs(base) * 0.10:
        return best_swap
    return None


def find_best_delete(board):
    """Pick the tile value whose deletion most improves the board evaluation.
    Only considers tiles <= mt/4 to avoid deleting high-value tiles."""
    positions = {}
    for r in range(4):
        for c in range(4):
            v = board[r][c]
            if v > 0:
                positions.setdefault(v, []).append((r, c))
    mt = max_tile(board)
    best_gain, best_info = -float("inf"), None
    for v, cells in positions.items():
        if v > mt // 4:
            continue
        # Simulate deleting all tiles of this value
        nb = [row[:] for row in board]
        for r, c in cells:
            nb[r][c] = 0
        gain = evaluate(nb) - evaluate(board)
        if gain > best_gain:
            best_gain = gain
            best_info = (v, cells[0][0], cells[0][1])
    return best_info


# ── Main game loop ────────────────────────────────────────────────

def main():
    print("Starting 2048 AI…\n")
    options = webdriver.ChromeOptions()
    options.add_argument("--window-size=500,900")
    options.add_argument("--window-position=50,50")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--disable-notifications")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    driver = webdriver.Chrome(options=options)
    driver.set_script_timeout(10)  # prevent hanging on execute_script

    try:
        driver.get(URL)
        print("Waiting for page to load…")
        time.sleep(7)

        # Dismiss welcome banner — click the small round X close button,
        # NOT the "Play Tutorial" button (both share bg-near-black class).
        banner_closed = False
        for attempt in range(3):
            try:
                # Strategy 1: small round button
                for b in driver.find_elements(By.CSS_SELECTOR,
                        "button.rounded-full, button[class*='close'], "
                        "button[class*='dismiss']"):
                    if b.is_displayed() and b.size["width"] <= 40:
                        b.click()
                        banner_closed = True
                        print("✓ Closed welcome banner")
                        time.sleep(0.5)
                        break
                if banner_closed:
                    break
                # Strategy 2: find X/close button by content (not tutorial)
                for b in driver.find_elements(By.CSS_SELECTOR, "button"):
                    if b.is_displayed():
                        txt = b.text.strip().lower()
                        w = b.size["width"]
                        # Skip "Play Tutorial" and large buttons
                        if "tutorial" in txt or "play" in txt:
                            continue
                        # Small button or button with X/× content
                        if w <= 40 or txt in ("x", "×", "✕", ""):
                            if w <= 60:
                                b.click()
                                banner_closed = True
                                print("✓ Closed welcome banner (strategy 2)")
                                time.sleep(0.5)
                                break
                if banner_closed:
                    break
                time.sleep(1)
            except Exception:
                time.sleep(1)
        if not banner_closed:
            # Strategy 3: click outside the banner to dismiss
            try:
                driver.execute_script(
                    "var c=document.querySelector('canvas');"
                    "if(c) c.click();")
                print("⚠ Banner not found, clicked canvas")
            except Exception:
                pass

        # Dismiss overlays
        for sel in ["[id*='consent'] button", ".fc-cta-consent",
                    "#ez-accept-all", "button[class*='accept']"]:
            try:
                for b in driver.find_elements(By.CSS_SELECTOR, sel):
                    if b.is_displayed():
                        b.click()
                        time.sleep(0.3)
            except Exception:
                pass

        # Focus game
        try:
            driver.find_element(By.ID, "app").click()
        except Exception:
            driver.find_element(By.TAG_NAME, "body").click()
        time.sleep(0.5)

        board = read_board(driver)
        tiles = sum(1 for r in range(4) for c in range(4) if board and board[r][c] > 0)

        if board and 1 <= tiles <= 4:
            charges = get_charges(driver)
            print(f"✓ Board readable — expectimax AI with power-ups")
            print(f"  Power-ups: undo={charges[0]} swap={charges[1]} "
                  f"delete={charges[2]}")
            print_board(board, "Initial board")
            play_ai(driver)
        else:
            print("✗ Board not readable (%d tiles)." % (tiles or 0))

        try:
            input("\nPress Enter to close the browser…")
        except EOFError:
            pass
    finally:
        driver.quit()


def play_ai(driver):
    move_num = 0
    same_count = 0
    prev_board = None
    won = False
    tracked_board = None  # Computed board state — more reliable than pixels
    prev_tracked_ref = None  # Last known good tracking for gold tile recovery

    def dismiss_dialogs():
        """Close any popup/modal/overlay/ad that might be blocking input.
        
        IMPORTANT: Do NOT press Escape (opens game menu) and do NOT use
        broad CSS selectors (could click game power-up buttons).
        Just click on the canvas to regain focus, and try to dismiss
        any game-over overlay or ads.
        """
        try:
            driver.execute_script("""
                document.querySelectorAll('iframe').forEach(f => f.remove());
                document.querySelectorAll(
                    '[id*="ad"], [id*="Ad"], [class*="ad-"], [class*="Ad-"], ' +
                    '[id*="google_ads"], [id*="aswift"], [class*="overlay"]'
                ).forEach(el => {
                    if (!el.closest('canvas') && !el.querySelector('canvas'))
                        el.remove();
                });
                document.querySelectorAll('div').forEach(d => {
                    var s = window.getComputedStyle(d);
                    if ((s.position === 'fixed' || s.position === 'absolute') &&
                        parseInt(s.zIndex) > 999 &&
                        d.offsetWidth > window.innerWidth * 0.5 &&
                        d.offsetHeight > window.innerHeight * 0.3 &&
                        !d.querySelector('canvas')) {
                        d.remove();
                    }
                });
            """)
        except Exception:
            pass
        try:
            for sel in ["[class*='keep']", "[class*='continu']", "[class*='retry']",
                        "[class*='try-again']", "[class*='close']"]:
                for btn in driver.find_elements(By.CSS_SELECTOR, sel):
                    if btn.is_displayed() and btn.tag_name in ("button", "a", "div"):
                        try:
                            btn.click()
                            time.sleep(0.3)
                        except Exception:
                            pass
            canvas = driver.find_elements(By.CSS_SELECTOR, "canvas")
            if canvas and canvas[0].is_displayed():
                driver.execute_script(
                    "arguments[0].focus(); arguments[0].click();", canvas[0])
            time.sleep(0.3)
        except Exception:
            pass

    def reconcile_board(expected, actual):
        """Reconcile expected (computed) board with actual (pixel-read) board.
        
        Trust computed state for gold tiles (128-2048) where color reading
        is unreliable. Trust pixels for new random tiles (2/4) and
        non-gold tiles. Returns corrected board.
        """
        if expected is None or actual is None:
            return actual
        corrected = [row[:] for row in expected]
        for r in range(4):
            for c in range(4):
                e, a = expected[r][c], actual[r][c]
                if e == a:
                    continue
                if e == 0 and a in (2, 4):
                    # New random tile spawned — trust pixels
                    corrected[r][c] = a
                elif e == 0 and a > 0:
                    # Unexpected tile where empty expected — trust pixels
                    corrected[r][c] = a
                elif e >= 128 and a >= 128:
                    # Gold tile disagreement — trust computation
                    corrected[r][c] = e
                else:
                    # Other disagreement — trust pixels
                    corrected[r][c] = a
        return corrected

    try:
     while True:
        pixel_board = read_board(driver)
        if pixel_board is None:
            dismiss_dialogs()
            time.sleep(0.5)
            for retry in range(5):
                pixel_board = read_board(driver)
                if pixel_board is not None:
                    break
                print(f"Board read failed — retry {retry+1}/5…")
                dismiss_dialogs()
                time.sleep(1 + retry)
            if pixel_board is None:
                print("Persistent read failure.")
                return

        # Use tracked state if available, otherwise bootstrap from pixels
        if tracked_board is not None:
            board = [row[:] for row in tracked_board]
        else:
            # Bootstrap — use reconcile to preserve gold tile values
            if prev_tracked_ref is not None and pixel_board:
                board = reconcile_board(prev_tracked_ref, pixel_board)
            else:
                board = pixel_board
            if board:
                tracked_board = [row[:] for row in board]

        # ── Stuck detection (use pixel board to avoid tracking feedback loop) ──
        if pixel_board == prev_board:
            same_count += 1
            if same_count <= 3:
                time.sleep(0.3)
                continue
            if same_count == 4:
                time.sleep(0.5)
                pixel_board = read_board(driver)
                if pixel_board and pixel_board != prev_board:
                    # Board changed on re-read — keep tracking intact
                    same_count = 0
                    continue
            if same_count > 4 and same_count <= 8:
                if same_count == 5:
                    try:
                        colors = driver.execute_script(JS_READ_BOARD)
                        if colors:
                            print(f"  🔍 Raw colors: {['#%06x→%d' % (c, packed_to_value(c)) for c in colors]}")
                    except Exception:
                        pass
                    print(f"  ⚠ Board unchanged {same_count}x — trying recovery…")
                dismiss_dialogs()
                time.sleep(0.5)
                any_moved = False
                for d in DIRECTIONS:
                    send_key(driver, d)
                    time.sleep(MOVE_DELAY + 0.2)
                    new_board = read_board(driver)
                    if new_board and new_board != pixel_board:
                        any_moved = True
                        # Try to maintain tracking through recovery
                        if tracked_board is not None:
                            exp, _, sm = simulate_move(tracked_board, d)
                            if sm:
                                spawns = [(r, c)
                                          for r in range(4) for c in range(4)
                                          if exp[r][c] == 0
                                          and new_board[r][c] in (2, 4)]
                                if spawns:
                                    for r, c in spawns:
                                        exp[r][c] = new_board[r][c]
                                    tracked_board = exp
                                else:
                                    if tracked_board is not None:
                                        prev_tracked_ref = [row[:] for row in tracked_board]
                                    tracked_board = None
                            else:
                                if tracked_board is not None:
                                    prev_tracked_ref = [row[:] for row in tracked_board]
                                tracked_board = None
                        move_num += 1
                        same_count = 0
                        break
                if any_moved:
                    continue
            if same_count > 8 and same_count <= 15:
                # Try power-ups only in the first few stuck cycles
                charges = get_charges(driver)
                if charges[0] > 0:
                    print(f"  ⚡ UNDO to escape stuck state")
                    if use_undo(driver):
                        if tracked_board is not None:
                            prev_tracked_ref = [row[:] for row in tracked_board]
                        tracked_board = None
                        same_count = 0
                        time.sleep(0.5)
                        continue
                if charges[2] > 0:
                    info = find_best_delete(board)
                    if info:
                        val, dr, dc = info
                        print(f"  ⚡ DELETE {val} tiles (charge left) "
                              f"to avoid game over")
                        if use_delete(driver, dr, dc):
                            if tracked_board is not None:
                                prev_tracked_ref = [row[:] for row in tracked_board]
                            tracked_board = None
                            same_count = 0
                            continue
                        # Delete failed — don't reset same_count
            if same_count > 12:
                # Truly dead — no recovery after 12 iterations
                mt = max_tile(board)
                print(f"\n{'='*48}")
                print(f"  GAME OVER — Best tile: {mt}  Moves: {move_num}")
                print(f"{'='*48}")
                print_board(board, "Final board")
                return
        else:
            same_count = 0
        prev_board = [row[:] for row in pixel_board]

        mt = max_tile(board)
        ec = empty_count(board)

        # ── Win detection ──
        if mt >= 2048 and not won:
            won = True
            print(f"\n{'*'*48}")
            print(f"  ★  YOU WIN!  Reached {mt}!  ({move_num} moves)  ★")
            print(f"{'*'*48}")
            print_board(board, "Winning board")
            try:
                for b in driver.find_elements(By.CSS_SELECTOR,
                        "button, [class*='keep'], [class*='continue']"):
                    if b.is_displayed() and (
                        "keep" in b.text.lower() or "continu" in b.text.lower()
                    ):
                        b.click()
                        time.sleep(0.5)
                        break
            except Exception:
                pass
            print("Continuing to play for a higher score…\n")

        # ── Adaptive depth ──
        if _RUST_DLL is not None:
            if ec >= 10:
                depth = 6
            elif ec >= 6:
                depth = 7
            elif ec >= 3:
                depth = 8
            else:
                depth = 9
        else:
            if ec >= 8:
                depth = 3
            elif ec >= 4:
                depth = 4
            else:
                depth = 5

        # ── Pick best move ──
        t0 = time.time()
        moves = ranked_moves(board, depth)
        think_ms = (time.time() - t0) * 1000

        if not moves:
            # No valid moves — try power-ups (only once, avoid loop)
            charges = get_charges(driver)
            if charges[2] > 0 and same_count < 3:
                info = find_best_delete(board)
                if info:
                    val, dr, dc = info
                    print(f"  ⚡ DELETE {val} tiles — no moves available")
                    if use_delete(driver, dr, dc):
                        if tracked_board is not None:
                            prev_tracked_ref = [row[:] for row in tracked_board]
                        tracked_board = None
                        continue
            # Delete failed or unavailable — this is truly game over
            mt = max_tile(board)
            print(f"\n{'='*48}")
            print(f"  GAME OVER — Best tile: {mt}  Moves: {move_num}")
            print(f"{'='*48}")
            print_board(board, "Final board")
            return

        direction = moves[0][1]
        move_num += 1

        if move_num <= 5 or move_num % 25 == 0 or mt >= 512 or ec <= 3:
            extra = ""
            if move_num % 25 == 0:
                charges = get_charges(driver)
                extra = f" ⚡{charges[0]}/{charges[1]}/{charges[2]}"
            print_board(board, f"Move {move_num} → {direction}  "
                        f"(best: {mt}, empty: {ec}, depth: {depth}, "
                        f"{think_ms:.0f}ms){extra}")

        send_key(driver, direction)
        time.sleep(MOVE_DELAY)

        # ── State tracking: compute expected, read actual, verify ──
        expected_board, _, moved = simulate_move(board, direction)
        new_board = read_board(driver)

        # Detect if move didn't register (pixel board unchanged)
        if new_board and new_board == pixel_board:
            send_key(driver, direction)
            time.sleep(MOVE_DELAY + 0.1)
            new_board = read_board(driver)
            if new_board and new_board == pixel_board:
                continue

        # Verify move by checking for new 2/4 tile spawn
        if new_board and tracked_board is not None and moved:
            # Find cells where expected is empty but pixel shows 2 or 4
            new_spawns = [(r, c) for r in range(4) for c in range(4)
                          if expected_board[r][c] == 0
                          and new_board[r][c] in (2, 4)]
            if len(new_spawns) >= 1:
                # Move confirmed — use computed board + new tile
                result = [row[:] for row in expected_board]
                for r, c in new_spawns:
                    result[r][c] = new_board[r][c]
                new_board = result
            else:
                # No new tile found — use reconcile to preserve gold values
                fresh = read_board(driver)
                if fresh:
                    new_board = reconcile_board(expected_board, fresh)
                else:
                    new_board = [row[:] for row in expected_board]
        elif new_board and tracked_board is None:
            # Bootstrapping — use reconcile if we have a previous reference
            if prev_tracked_ref is not None:
                new_board = reconcile_board(prev_tracked_ref, new_board)
            # else just use pixel board as-is

        # ── Post-move evaluation → possible undo ──
        if new_board and should_undo(board, new_board, moves):
            charges = get_charges(driver)
            if charges[0] > 0:
                alt_dir = moves[1][1]
                print(f"  ↩ UNDO move {move_num} ({direction}) → "
                      f"{alt_dir}  [undo left: {charges[0]-1}]")
                use_undo(driver)
                # After undo, reset tracking — re-read from pixels
                if tracked_board is not None:
                    prev_tracked_ref = [row[:] for row in tracked_board]
                tracked_board = None
                send_key(driver, alt_dir)
                time.sleep(MOVE_DELAY)
                new_board = read_board(driver)
                if new_board:
                    if prev_tracked_ref is not None:
                        new_board = reconcile_board(prev_tracked_ref, new_board)
                    tracked_board = [row[:] for row in new_board]

        # Update tracked state
        if new_board:
            tracked_board = [row[:] for row in new_board]

        # ── Proactive power-ups (BEFORE game-over overlay appears) ──
        if new_board:
            ec_new = empty_count(new_board)
            mt_new = max_tile(new_board)

            # Delete: when board is full or nearly full
            if ec_new <= 1 and mt_new >= 128:
                charges = get_charges(driver)
                if charges[2] > 0:
                    any_valid = any(
                        simulate_move(new_board, d)[2]
                        for d in DIRECTIONS)
                    if not any_valid or ec_new == 0:
                        info = find_best_delete(new_board)
                        if info:
                            print(f"  ⚡ PROACTIVE DELETE {info[0]} "
                                  f"(empty={ec_new}, charges={charges[2]})")
                            if use_delete(driver, info[1], info[2]):
                                if tracked_board is not None:
                                    prev_tracked_ref = [row[:] for row in tracked_board]
                                tracked_board = None
                                time.sleep(MOVE_DELAY)
                                continue

            # Swap: ONLY during stuck recovery (reactive, never proactive).
            # Proactive swap disabled — unreliable cell clicks and
            # unpredictable game mechanics destroyed a 256 tile in testing.

        # ── Periodic ad/dialog dismissal (every 20 moves) ──
        if move_num % 20 == 0:
            try:
                driver.execute_script(
                    "document.querySelectorAll('iframe').forEach(f=>f.remove());"
                )
            except Exception:
                pass
            dismiss_dialogs()
    except Exception as e:
        import traceback
        print(f"\n⚠ play_ai exception: {e}")
        traceback.print_exc()
        print(f"  Last move: {move_num}, tracked_board: {tracked_board is not None}")


if __name__ == "__main__":
    main()
