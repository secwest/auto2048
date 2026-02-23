/// Fast expectimax search engine for 2048 with transposition table.
/// Board stored as u64 bitboard (4 bits per cell, log2 values).
/// Exported via C ABI for ctypes.

use std::collections::HashMap;
use std::cell::RefCell;

// Geometric (1.5^n) snake weights — steep gradient along snake path.
// pos 0 = 1.0, pos 15 = 437.9.  Much stronger than linear 1–16.
const WEIGHT_MATRICES: [[[f64; 4]; 4]; 8] = [
    // Corner at (3,0) — snake right then left
    [[  1.000,   1.500,   2.250,   3.375],
     [ 17.086,  11.391,   7.594,   5.063],
     [ 25.629,  38.443,  57.665,  86.498],
     [437.894, 291.929, 194.620, 129.746]],
    // Corner at (3,3) — snake left then right
    [[  3.375,   2.250,   1.500,   1.000],
     [  5.063,   7.594,  11.391,  17.086],
     [ 86.498,  57.665,  38.443,  25.629],
     [129.746, 194.620, 291.929, 437.894]],
    // Corner at (0,0)
    [[437.894, 291.929, 194.620, 129.746],
     [ 25.629,  38.443,  57.665,  86.498],
     [ 17.086,  11.391,   7.594,   5.063],
     [  1.000,   1.500,   2.250,   3.375]],
    // Corner at (0,3)
    [[129.746, 194.620, 291.929, 437.894],
     [ 86.498,  57.665,  38.443,  25.629],
     [  5.063,   7.594,  11.391,  17.086],
     [  3.375,   2.250,   1.500,   1.000]],
    // Column-wise: corner at (0,0)
    [[437.894,  25.629,  17.086,   1.000],
     [291.929,  38.443,  11.391,   1.500],
     [194.620,  57.665,   7.594,   2.250],
     [129.746,  86.498,   5.063,   3.375]],
    // Column-wise: corner at (0,3)
    [[  1.000,  17.086,  25.629, 437.894],
     [  1.500,  11.391,  38.443, 291.929],
     [  2.250,   7.594,  57.665, 194.620],
     [  3.375,   5.063,  86.498, 129.746]],
    // Column-wise: corner at (3,0)
    [[129.746,  86.498,   5.063,   3.375],
     [194.620,  57.665,   7.594,   2.250],
     [291.929,  38.443,  11.391,   1.500],
     [437.894,  25.629,  17.086,   1.000]],
    // Column-wise: corner at (3,3)
    [[  3.375,   5.063,  86.498, 129.746],
     [  2.250,   7.594,  57.665, 194.620],
     [  1.500,  11.391,  38.443, 291.929],
     [  1.000,  17.086,  25.629, 437.894]],
];

const MAX_CHANCE_CELLS: usize = 6;

type Board = [[u16; 4]; 4];

// Transposition table: board hash → (depth, score)
thread_local! {
    static TT: RefCell<HashMap<u64, (u32, f64)>> = RefCell::new(HashMap::with_capacity(1 << 20));
}

#[inline]
fn board_hash(board: &Board) -> u64 {
    let mut h: u64 = 0;
    for r in 0..4 {
        for c in 0..4 {
            let v = board[r][c];
            let bits = if v == 0 { 0u64 } else { (v as f64).log2() as u64 };
            h |= (bits & 0xF) << ((r * 4 + c) * 4);
        }
    }
    h
}

#[inline]
fn log2v(v: u16) -> f64 {
    if v == 0 { return 0.0; }
    (v as f64).log2()
}

fn compress_line(line: &[u16; 4]) -> ([u16; 4], f64) {
    let mut tiles = [0u16; 4];
    let mut tc = 0;
    for &v in line { if v != 0 { tiles[tc] = v; tc += 1; } }
    let mut merged = [0u16; 4];
    let mut score = 0.0;
    let mut i = 0;
    let mut out = 0;
    while i < tc {
        if i + 1 < tc && tiles[i] == tiles[i + 1] {
            let m = tiles[i] * 2;
            merged[out] = m;
            score += m as f64;
            i += 2;
        } else {
            merged[out] = tiles[i];
            i += 1;
        }
        out += 1;
    }
    (merged, score)
}

fn simulate_move(board: &Board, dir: u8) -> (Board, f64, bool) {
    let mut nb = *board;
    let mut total = 0.0;
    let mut moved = false;

    for i in 0..4 {
        match dir {
            0 => { // up
                let line = [nb[0][i], nb[1][i], nb[2][i], nb[3][i]];
                let (res, sc) = compress_line(&line);
                if res != line { moved = true; }
                for r in 0..4 { nb[r][i] = res[r]; }
                total += sc;
            }
            1 => { // down
                let line = [nb[3][i], nb[2][i], nb[1][i], nb[0][i]];
                let (res, sc) = compress_line(&line);
                let rev = [res[3], res[2], res[1], res[0]];
                let orig = [nb[0][i], nb[1][i], nb[2][i], nb[3][i]];
                if rev != orig { moved = true; }
                for r in 0..4 { nb[r][i] = rev[r]; }
                total += sc;
            }
            2 => { // left
                let line = nb[i];
                let (res, sc) = compress_line(&line);
                if res != nb[i] { moved = true; }
                nb[i] = res;
                total += sc;
            }
            3 => { // right
                let line = [nb[i][3], nb[i][2], nb[i][1], nb[i][0]];
                let (res, sc) = compress_line(&line);
                let rev = [res[3], res[2], res[1], res[0]];
                if rev != nb[i] { moved = true; }
                nb[i] = rev;
                total += sc;
            }
            _ => {}
        }
    }
    (nb, total, moved)
}

fn evaluate(board: &Board) -> f64 {
    // 1) Snake pattern — best of 8 orientations, steep geometric gradient
    let mut snake = f64::NEG_INFINITY;
    for w in &WEIGHT_MATRICES {
        let mut s = 0.0;
        for r in 0..4 {
            for c in 0..4 {
                let lv = log2v(board[r][c]);
                s += lv * lv * w[r][c];
            }
        }
        if s > snake { snake = s; }
    }

    // 2) Empty cells — critical for survival; steeper penalty near zero
    let empty: usize = board.iter().flatten().filter(|&&v| v == 0).count();
    let empty_score = match empty {
        0  => -800000.0,
        1  => 3000.0,
        2  => 12000.0,
        _  => empty as f64 * empty as f64 * 2000.0,
    };

    // 3) Max tile in corner
    let mt = *board.iter().flatten().max().unwrap();
    let mt_log = log2v(mt);
    let corners = [board[0][0], board[0][3], board[3][0], board[3][3]];
    let in_corner = corners.contains(&mt);
    let corner_score = if in_corner {
        mt_log * mt_log * 500.0
    } else {
        // Check if at least on an edge
        let on_edge =
            (0..4).any(|c| board[0][c] == mt) ||
            (0..4).any(|c| board[3][c] == mt) ||
            (0..4).any(|r| board[r][0] == mt) ||
            (0..4).any(|r| board[r][3] == mt);
        if on_edge { -(mt_log * mt_log * 1000.0) }
        else       { -(mt_log * mt_log * 3000.0) }
    };

    // 4) Scatter penalty — non-adjacent duplicate high tiles
    let mut scatter_penalty = 0.0;
    let mut positions: [(usize, usize); 16] = [(0, 0); 16];
    let mut pos_count = 0;
    for r in 0..4 {
        for c in 0..4 {
            if board[r][c] >= 64 {
                positions[pos_count] = (r, c);
                pos_count += 1;
            }
        }
    }
    for i in 0..pos_count {
        for j in (i + 1)..pos_count {
            let v1 = board[positions[i].0][positions[i].1];
            let v2 = board[positions[j].0][positions[j].1];
            if v1 == v2 {
                let dr = (positions[i].0 as i32 - positions[j].0 as i32).abs();
                let dc = (positions[i].1 as i32 - positions[j].1 as i32).abs();
                if dr + dc != 1 {
                    let lv = log2v(v1);
                    scatter_penalty -= lv * lv * 2000.0;
                }
            }
        }
    }

    // 5) Monotonicity — measure how well rows/cols are sorted
    let mut mono = 0.0;
    for r in 0..4 {
        let mut inc = 0.0;
        let mut dec = 0.0;
        for c in 0..3 {
            let cur = log2v(board[r][c]);
            let nxt = log2v(board[r][c + 1]);
            if cur > nxt { dec += nxt - cur; }
            else { inc += cur - nxt; }
        }
        mono += inc.max(dec);
    }
    for c in 0..4 {
        let mut inc = 0.0;
        let mut dec = 0.0;
        for r in 0..3 {
            let cur = log2v(board[r][c]);
            let nxt = log2v(board[r + 1][c]);
            if cur > nxt { dec += nxt - cur; }
            else { inc += cur - nxt; }
        }
        mono += inc.max(dec);
    }

    // 6) Smoothness — adjacent tiles should be similar
    let mut smooth = 0.0;
    for r in 0..4 {
        for c in 0..3 {
            if board[r][c] != 0 && board[r][c + 1] != 0 {
                smooth -= (log2v(board[r][c]) - log2v(board[r][c + 1])).abs();
            }
        }
    }
    for c in 0..4 {
        for r in 0..3 {
            if board[r][c] != 0 && board[r + 1][c] != 0 {
                smooth -= (log2v(board[r][c]) - log2v(board[r + 1][c])).abs();
            }
        }
    }

    // 7) Merge potential — adjacent equal tiles (weighted by value)
    //    Stronger bonus for high-value merges (512+512, 256+256, etc.)
    let mut merges = 0.0;
    for r in 0..4 {
        for c in 0..4 {
            let v = board[r][c];
            if v == 0 { continue; }
            let lv = log2v(v);
            let weight = if v >= 256 { lv * lv * lv } else { lv * lv };
            if c + 1 < 4 && board[r][c + 1] == v { merges += weight; }
            if r + 1 < 4 && board[r + 1][c] == v { merges += weight; }
        }
    }

    // 8) Chain bonus — reward descending neighbors from the max tile
    //    e.g. 1024→512→256→128 in adjacent cells
    let mut chain_bonus = 0.0;
    if in_corner && mt >= 64 {
        // Find corner with max tile
        let corner_pos: [(usize, usize); 4] = [(0,0), (0,3), (3,0), (3,3)];
        for &(cr, cc) in &corner_pos {
            if board[cr][cc] != mt { continue; }
            // Follow chain from corner
            let mut cur_r = cr;
            let mut cur_c = cc;
            let mut cur_val = mt;
            let mut chain_len = 0;
            'chain: loop {
                let target = cur_val / 2;
                if target == 0 { break; }
                let neighbors: [(i32, i32); 4] = [(-1,0),(1,0),(0,-1),(0,1)];
                for &(dr, dc) in &neighbors {
                    let nr = cur_r as i32 + dr;
                    let nc = cur_c as i32 + dc;
                    if nr >= 0 && nr < 4 && nc >= 0 && nc < 4 {
                        if board[nr as usize][nc as usize] == target {
                            cur_r = nr as usize;
                            cur_c = nc as usize;
                            cur_val = target;
                            chain_len += 1;
                            let lv = log2v(target);
                            chain_bonus += lv * lv * 500.0;
                            continue 'chain;
                        }
                    }
                }
                break;
            }
            if chain_len > 0 { break; }
        }
    }

    snake * 5.0 + empty_score + corner_score + scatter_penalty
        + mono * 600.0 + smooth * 250.0 + merges * 800.0 + chain_bonus
}

fn expectimax(board: &Board, depth: u32, is_max: bool) -> f64 {
    if depth == 0 {
        return evaluate(board);
    }

    // Check transposition table for chance nodes (most repeated)
    if !is_max {
        let h = board_hash(board);
        let cached = TT.with(|tt| {
            if let Some(&(d, s)) = tt.borrow().get(&h) {
                if d >= depth { return Some(s); }
            }
            None
        });
        if let Some(s) = cached { return s; }
    }

    if is_max {
        let mut best = f64::NEG_INFINITY;
        for d in 0..4u8 {
            let (nb, ms, moved) = simulate_move(board, d);
            if !moved { continue; }
            let v = expectimax(&nb, depth - 1, false) + ms;
            if v > best { best = v; }
        }
        if best == f64::NEG_INFINITY { evaluate(board) } else { best }
    } else {
        let mut empty: Vec<(usize, usize)> = Vec::new();
        for r in 0..4 {
            for c in 0..4 {
                if board[r][c] == 0 {
                    empty.push((r, c));
                }
            }
        }
        if empty.is_empty() {
            return evaluate(board);
        }
        let cells: Vec<(usize, usize)> = if empty.len() > MAX_CHANCE_CELLS {
            let mut scored: Vec<(i32, usize, usize)> = empty.iter().map(|&(r, c)| {
                let adj = [(0isize, 1isize), (0, -1), (1, 0), (-1, 0)]
                    .iter()
                    .filter(|&&(dr, dc)| {
                        let nr = r as isize + dr;
                        let nc = c as isize + dc;
                        nr >= 0 && nr < 4 && nc >= 0 && nc < 4
                            && board[nr as usize][nc as usize] > 0
                    })
                    .count() as i32;
                (-adj, r, c)
            }).collect();
            scored.sort();
            scored[..MAX_CHANCE_CELLS].iter().map(|&(_, r, c)| (r, c)).collect()
        } else {
            empty
        };

        let mut total = 0.0;
        for &(r, c) in &cells {
            for &(val, prob) in &[(2u16, 0.9), (4u16, 0.1)] {
                let mut nb = *board;
                nb[r][c] = val;
                total += prob * expectimax(&nb, depth - 1, true);
            }
        }
        let result = total / cells.len() as f64;

        // Store in transposition table
        let h = board_hash(board);
        TT.with(|tt| {
            let mut t = tt.borrow_mut();
            t.insert(h, (depth, result));
            // Evict if too large
            if t.len() > (1 << 21) {
                t.clear();
            }
        });

        result
    }
}

/// C ABI: given board (16 u16s) and depth, write ranked moves.
/// Returns number of valid moves. Directions: 0=up, 1=down, 2=left, 3=right.
#[no_mangle]
pub extern "C" fn search_ranked_moves(
    board_ptr: *const u16,
    depth: u32,
    scores_out: *mut f64,
    dirs_out: *mut u8,
) -> u32 {
    let board_flat = unsafe { std::slice::from_raw_parts(board_ptr, 16) };
    let mut board = [[0u16; 4]; 4];
    for i in 0..16 {
        board[i / 4][i % 4] = board_flat[i];
    }

    // Clear TT at start of each top-level search
    TT.with(|tt| tt.borrow_mut().clear());

    let mut moves: Vec<(f64, u8)> = Vec::new();
    for d in 0..4u8 {
        let (nb, ms, moved) = simulate_move(&board, d);
        if !moved { continue; }
        let score = expectimax(&nb, depth, false) + ms as f64;
        moves.push((score, d));
    }
    moves.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap());

    let n = moves.len().min(4);
    let scores = unsafe { std::slice::from_raw_parts_mut(scores_out, 4) };
    let dirs = unsafe { std::slice::from_raw_parts_mut(dirs_out, 4) };
    for i in 0..n {
        scores[i] = moves[i].0;
        dirs[i] = moves[i].1;
    }
    n as u32
}
