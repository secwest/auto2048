/// Fast expectimax search engine for 2048 with bitboard and row lookup tables.
/// Board packed as u64 (4 bits per cell, log2 values).
/// Precomputed 65536-entry tables make moves and evaluation O(1) per row.
/// Based on nneonneo/xificurk architecture; achieves 10M+ states/sec.

use std::collections::HashMap;
use std::cell::RefCell;
use std::sync::Once;

type BB = u64;  // 16 nybbles: row0=bits[0:15], row1=[16:31], row2=[32:47], row3=[48:63]

const MAX_CHANCE: usize = 6;

// ── Lookup tables (filled once at startup) ──
static mut TBL_LEFT:  [u16; 65536] = [0; 65536];
static mut TBL_RIGHT: [u16; 65536] = [0; 65536];
static mut TBL_SCORE: [f64; 65536] = [0.0; 65536];  // merge score for left-move
static mut TBL_HEUR:  [f64; 65536] = [0.0; 65536];  // heuristic score per row
static INIT: Once = Once::new();

// Heuristic weights (nneonneo/xificurk CMA-ES optimized)
const W_LOST:   f64 = 200000.0;
const W_EMPTY:  f64 = 270000.0;
const W_MERGES: f64 = 700000.0;
const W_MONO:   f64 = 47000.0;
const W_SUM:    f64 = 11000.0;
const MONO_POW: i32 = 4;
const SUM_POW:  f64 = 3.5;

// Transposition table: bitboard → (depth, score)
thread_local! {
    static TT: RefCell<HashMap<BB, (u32, f64)>> = RefCell::new(HashMap::with_capacity(1 << 20));
}

fn init_tables() {
    INIT.call_once(|| {
        for rv in 0u32..65536 {
            let t = [
                (rv & 0xF) as u8,
                ((rv >> 4) & 0xF) as u8,
                ((rv >> 8) & 0xF) as u8,
                ((rv >> 12) & 0xF) as u8,
            ];

            // ── Row heuristic (nneonneo formula) ──
            let mut empty = 0.0f64;
            let mut merges = 0.0f64;
            let mut sum_val = 0.0f64;
            let mut mono_l = 0.0f64;
            let mut mono_r = 0.0f64;
            let mut prev: u8 = 0;
            let mut counter = 0i32;

            for i in 0..4 {
                if t[i] == 0 {
                    empty += 1.0;
                } else {
                    sum_val += (t[i] as f64).powf(SUM_POW);
                    if prev == t[i] {
                        counter += 1;
                    } else if prev != 0 {
                        merges += 1.0 + counter as f64;
                        counter = 0;
                    }
                    prev = t[i];
                }
                if i > 0 {
                    let a = (t[i - 1] as f64).powi(MONO_POW);
                    let b = (t[i] as f64).powi(MONO_POW);
                    if t[i - 1] > t[i] { mono_l += a - b; }
                    else if t[i] > t[i - 1] { mono_r += b - a; }
                }
            }
            if prev != 0 { merges += 1.0 + counter as f64; }

            let heur = -W_LOST
                + W_EMPTY * empty
                + W_MERGES * merges
                - W_MONO * mono_l.min(mono_r)
                - W_SUM * sum_val;

            unsafe { TBL_HEUR[rv as usize] = heur; }

            // ── Left move ──
            let mut line = [0u8; 4];
            let mut w = 0usize;
            for i in 0..4 { if t[i] != 0 { line[w] = t[i]; w += 1; } }

            let mut out = [0u8; 4];
            let mut score = 0.0f64;
            let mut i = 0usize;
            let mut o = 0usize;
            while i < 4 && line[i] != 0 {
                if i + 1 < 4 && line[i] == line[i + 1] {
                    let nr = line[i] + 1;
                    out[o] = if nr <= 15 { nr } else { 15 };
                    score += (1u64 << (nr as u32)) as f64;
                    i += 2;
                } else {
                    out[o] = line[i];
                    i += 1;
                }
                o += 1;
            }
            let left = (out[0] as u16) | ((out[1] as u16) << 4)
                     | ((out[2] as u16) << 8) | ((out[3] as u16) << 12);

            // ── Right move (reverse, left-compress, reverse) ──
            let rt = [t[3], t[2], t[1], t[0]];
            let mut rline = [0u8; 4];
            w = 0;
            for i in 0..4 { if rt[i] != 0 { rline[w] = rt[i]; w += 1; } }

            let mut rout = [0u8; 4];
            i = 0; o = 0;
            while i < 4 && rline[i] != 0 {
                if i + 1 < 4 && rline[i] == rline[i + 1] {
                    let nr = rline[i] + 1;
                    rout[o] = if nr <= 15 { nr } else { 15 };
                    i += 2;
                } else {
                    rout[o] = rline[i];
                    i += 1;
                }
                o += 1;
            }
            let right = (rout[3] as u16) | ((rout[2] as u16) << 4)
                       | ((rout[1] as u16) << 8) | ((rout[0] as u16) << 12);

            unsafe {
                TBL_LEFT[rv as usize] = left;
                TBL_RIGHT[rv as usize] = right;
                TBL_SCORE[rv as usize] = score;
            }
        }
    });
}

// ── Board primitives ──

#[inline(always)]
fn get_row(b: BB, r: usize) -> u16 {
    ((b >> (r << 4)) & 0xFFFF) as u16
}

#[inline(always)]
fn cell(b: BB, r: usize, c: usize) -> u8 {
    ((b >> ((r << 4) | (c << 2))) & 0xF) as u8
}

fn transpose(b: BB) -> BB {
    let mut r = 0u64;
    for row in 0..4u32 {
        for col in 0..4u32 {
            let v = (b >> (row * 16 + col * 4)) & 0xF;
            r |= v << (col * 16 + row * 4);
        }
    }
    r
}

#[inline]
fn reverse_row(r: u16) -> u16 {
    ((r & 0xF) << 12) | (((r >> 4) & 0xF) << 8)
    | (((r >> 8) & 0xF) << 4) | ((r >> 12) & 0xF)
}

// ── Moves via table lookup ──

fn move_left(b: BB) -> (BB, f64) {
    let mut r = 0u64;
    let mut s = 0.0;
    for i in 0..4 {
        let rv = get_row(b, i);
        unsafe {
            r |= (TBL_LEFT[rv as usize] as u64) << (i << 4);
            s += TBL_SCORE[rv as usize];
        }
    }
    (r, s)
}

fn move_right(b: BB) -> (BB, f64) {
    let mut r = 0u64;
    let mut s = 0.0;
    for i in 0..4 {
        let rv = get_row(b, i);
        unsafe {
            r |= (TBL_RIGHT[rv as usize] as u64) << (i << 4);
            s += TBL_SCORE[reverse_row(rv) as usize];
        }
    }
    (r, s)
}

fn do_move(b: BB, dir: u8) -> (BB, f64, bool) {
    let (nb, sc) = match dir {
        0 => { let t = transpose(b); let (m, s) = move_left(t); (transpose(m), s) }
        1 => { let t = transpose(b); let (m, s) = move_right(t); (transpose(m), s) }
        2 => move_left(b),
        3 => move_right(b),
        _ => (b, 0.0),
    };
    (nb, sc, nb != b)
}

// ── Evaluation ──

fn evaluate(b: BB) -> f64 {
    // Row + column heuristic from lookup tables (8 lookups)
    let t = transpose(b);
    let mut score = 0.0;
    for i in 0..4 {
        unsafe {
            score += TBL_HEUR[get_row(b, i) as usize];
            score += TBL_HEUR[get_row(t, i) as usize];
        }
    }

    // Board-level: corner bonus for max tile
    let mut max_rank = 0u8;
    let mut max_r = 0usize;
    let mut max_c = 0usize;
    for r in 0..4 {
        for c in 0..4 {
            let v = cell(b, r, c);
            if v > max_rank { max_rank = v; max_r = r; max_c = c; }
        }
    }

    if max_rank >= 7 {  // 128+
        let mr = max_rank as f64;
        let is_corner = (max_r == 0 || max_r == 3) && (max_c == 0 || max_c == 3);
        let is_edge = max_r == 0 || max_r == 3 || max_c == 0 || max_c == 3;

        if is_corner {
            score += mr * mr * 5000.0;
        } else if is_edge {
            score -= mr * mr * 8000.0;
        } else {
            score -= mr * mr * 20000.0;
        }
    }

    score
}

// ── Expectimax search ──

fn expectimax(board: BB, depth: u32, is_max: bool) -> f64 {
    if depth == 0 { return evaluate(board); }

    if !is_max {
        let cached = TT.with(|tt| {
            if let Some(&(d, s)) = tt.borrow().get(&board) {
                if d >= depth { return Some(s); }
            }
            None
        });
        if let Some(s) = cached { return s; }
    }

    if is_max {
        let mut best = f64::NEG_INFINITY;
        for d in 0..4u8 {
            let (nb, ms, moved) = do_move(board, d);
            if !moved { continue; }
            let v = expectimax(nb, depth - 1, false) + ms;
            if v > best { best = v; }
        }
        if best == f64::NEG_INFINITY { evaluate(board) } else { best }
    } else {
        // Collect empty cells
        let mut empties: Vec<usize> = Vec::with_capacity(16);
        for i in 0..16 {
            if (board >> (i * 4)) & 0xF == 0 {
                empties.push(i);
            }
        }
        if empties.is_empty() { return evaluate(board); }

        // Limit to most critical cells when too many empties
        let cells: Vec<usize> = if empties.len() > MAX_CHANCE {
            let mut scored: Vec<(i32, usize)> = empties.iter().map(|&pos| {
                let r = pos / 4;
                let c = pos % 4;
                let mut adj = 0i32;
                for &(dr, dc) in &[(-1i32, 0i32), (1, 0), (0, -1), (0, 1)] {
                    let nr = r as i32 + dr;
                    let nc = c as i32 + dc;
                    if nr >= 0 && nr < 4 && nc >= 0 && nc < 4 {
                        let idx = nr as usize * 4 + nc as usize;
                        if (board >> (idx * 4)) & 0xF != 0 { adj += 1; }
                    }
                }
                (-adj, pos)
            }).collect();
            scored.sort();
            scored[..MAX_CHANCE].iter().map(|&(_, p)| p).collect()
        } else {
            empties
        };

        let mut total = 0.0;
        for &pos in &cells {
            let shift = (pos * 4) as u64;
            let nb2 = board | (1u64 << shift);  // rank 1 = tile 2
            total += 0.9 * expectimax(nb2, depth - 1, true);
            let nb4 = board | (2u64 << shift);  // rank 2 = tile 4
            total += 0.1 * expectimax(nb4, depth - 1, true);
        }
        let result = total / cells.len() as f64;

        // Cache in TT (bitboard IS its own hash — collision-free)
        TT.with(|tt| {
            let mut t = tt.borrow_mut();
            t.insert(board, (depth, result));
            if t.len() > (1 << 22) { t.clear(); }
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
    init_tables();

    // Convert u16 tile values → bitboard (log2 ranks)
    let flat = unsafe { std::slice::from_raw_parts(board_ptr, 16) };
    let mut board: BB = 0;
    for i in 0..16 {
        let val = flat[i];
        let rank = if val == 0 { 0u64 } else { (val as f64).log2() as u64 };
        board |= (rank & 0xF) << (i * 4);
    }

    TT.with(|tt| tt.borrow_mut().clear());

    let mut moves: Vec<(f64, u8)> = Vec::new();
    for d in 0..4u8 {
        let (nb, ms, moved) = do_move(board, d);
        if !moved { continue; }
        let score = expectimax(nb, depth, false) + ms;
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
