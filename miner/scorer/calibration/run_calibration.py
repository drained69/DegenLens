"""Score every calibration WASM against the local corpus and compare to
the node's published eval_score. Prints a table so we can see whether local
ordering agrees with node ordering — the whole point of the exercise.

The local proxy the README boasts about ranked v4 as best; the node scored v4
0.5292, below v2's 0.5967. Any lib.rs tuning driven by a corpus that mis-ranks
that badly is producing noise. This script's job is to expose that.
"""
from __future__ import annotations
import os, sys, json, glob, statistics
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
from wasm_harness import Scorer
from corpus import CASES


# Registered wasm -> known node eval_score.
NODE_SCORES = {
    'reg642': 0.7923,
    'reg940': 0.7245,
    'reg942': 0.7124,
    'reg617': 0.6193,
    'reg633': 0.5963,
    'reg589': 0.5420,
    'reg551': 0.5967,   # our v2, if we have a copy
    'reg532': 0.5759,
    'reg710': 0.5292,   # our v4, if we have a copy
    'reg531': 0.4910,
    'reg600': 0.4853,
    'reg665': 0.2169,
    # Our binaries under different labels
    'v2':  0.5967,      # dist/degenlens_onchain_tx_lookup_v2.wasm (== reg 551)
    'v4':  0.5292,      # dist/degenlens_onchain_tx_lookup_v4.wasm (== reg 710)
    'v5':  None,        # pending (reg 810) — real score unknown
}


def run(label: str, wasm_path: str) -> dict:
    s = Scorer(wasm_path)
    goods, bads = [], []
    for c in CASES:
        gs = s.score(c['q'], c['gt'], c['good'])
        bs = s.score(c['q'], c['gt'], c['bad'])
        goods.append(gs); bads.append(bs)
    margin = statistics.fmean(goods) - statistics.fmean(bads)
    wins = sum(1 for g, b in zip(goods, bads) if g > b)
    return dict(
        label=label,
        margin=margin,
        wins=f'{wins}/{len(CASES)}',
        mean_good=statistics.fmean(goods),
        mean_bad=statistics.fmean(bads),
        goods=goods,
        bads=bads,
    )


def spearman(a: list[float], b: list[float]) -> float:
    """Rank-correlation between two same-length sequences."""
    if len(a) != len(b) or len(a) < 2:
        return float('nan')
    def rank(xs):
        # average-rank on ties
        idx = sorted(range(len(xs)), key=lambda i: xs[i])
        r = [0.0] * len(xs)
        i = 0
        while i < len(idx):
            j = i
            while j + 1 < len(idx) and xs[idx[j + 1]] == xs[idx[i]]:
                j += 1
            avg = (i + j + 2) / 2.0  # 1-indexed avg rank
            for k in range(i, j + 1):
                r[idx[k]] = avg
            i = j + 1
        return r
    ra, rb = rank(a), rank(b)
    ma = sum(ra) / len(ra); mb = sum(rb) / len(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    da = (sum((x - ma) ** 2 for x in ra)) ** 0.5
    db = (sum((y - mb) ** 2 for y in rb)) ** 0.5
    return num / (da * db) if da and db else float('nan')


def main():
    root = os.path.dirname(__file__)
    dist = os.path.join(root, '..', 'dist')
    wasms_dir = os.path.join(root, 'wasms')

    entries = []
    # Downloaded competitor wasms
    for path in sorted(glob.glob(os.path.join(wasms_dir, 'reg*.wasm'))):
        label = os.path.basename(path).replace('.wasm', '')
        if os.path.getsize(path) < 1000:
            print(f'skip {label} (not a real wasm, size={os.path.getsize(path)})')
            continue
        entries.append((label, path))
    # Our own binaries
    for name, path in [
        ('v2', os.path.join(dist, 'degenlens_onchain_tx_lookup_v2.wasm')),
        ('v4', os.path.join(dist, 'degenlens_onchain_tx_lookup_v4.wasm')),
        ('v5', os.path.join(dist, 'degenlens_onchain_tx_lookup_v5.wasm')),
        ('v6a', os.path.join(dist, 'degenlens_onchain_tx_lookup_v6a.wasm')),
        ('v6b', os.path.join(dist, 'degenlens_onchain_tx_lookup_v6b.wasm')),
        ('v6c', os.path.join(dist, 'degenlens_onchain_tx_lookup_v6c.wasm')),
        ('v6d', os.path.join(dist, 'degenlens_onchain_tx_lookup_v6d.wasm')),
    ]:
        if os.path.exists(path):
            entries.append((name, path))

    results = []
    for label, path in entries:
        try:
            r = run(label, path)
            r['node'] = NODE_SCORES.get(label)
            results.append(r)
            n = f'{r["node"]:.4f}' if r['node'] is not None else '—'
            print(f'{label:8} local_margin={r["margin"]:+.4f} wins={r["wins"]:>7} node={n}')
        except Exception as e:
            print(f'{label:8} ERROR {type(e).__name__}: {e}')

    # Local vs node rank agreement
    paired = [(r['margin'], r['node']) for r in results if r['node'] is not None]
    if len(paired) >= 3:
        rho = spearman([p[0] for p in paired], [p[1] for p in paired])
        print(f'\nLocal-vs-node Spearman: {rho:+.4f}  (over {len(paired)} labeled wasms)')

    # Save full per-case table for tuning work
    out = {
        'cases': [dict(q=c['q'], gt=c['gt'], good=c['good'], bad=c['bad']) for c in CASES],
        'results': [
            dict(label=r['label'], margin=r['margin'], wins=r['wins'], node=r['node'],
                 goods=r['goods'], bads=r['bads'])
            for r in results
        ],
    }
    with open(os.path.join(root, 'results.json'), 'w') as f:
        json.dump(out, f, indent=2)
    print('wrote', os.path.join(root, 'results.json'))


if __name__ == '__main__':
    main()
