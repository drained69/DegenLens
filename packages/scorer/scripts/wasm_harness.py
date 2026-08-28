"""Load a Telegraph scoring WASM and call rank_answer(q, gt, ma) from Python.

Mirrors what the devnode does: instantiate one module per call is optional but
we reuse a single instance per WASM here for speed. Every scoring wasm exports
`alloc(len) -> ptr`, `dealloc(ptr, len)`, `rank_answer(q, ql, gt, gtl, ma, mal) -> f32`.
"""
from __future__ import annotations
import wasmtime as wt


class Scorer:
    def __init__(self, wasm_path: str):
        cfg = wt.Config()
        # Champion binary uses SIMD; enable it.
        cfg.wasm_simd = True
        cfg.wasm_bulk_memory = True
        self.engine = wt.Engine(cfg)
        self.store = wt.Store(self.engine)
        self.module = wt.Module.from_file(self.engine, wasm_path)
        # These scoring wasms declare no imports.
        self.instance = wt.Instance(self.store, self.module, [])
        exports = self.instance.exports(self.store)
        self.memory: wt.Memory = exports["memory"]
        self.alloc = exports["alloc"]
        self.dealloc = exports["dealloc"]
        self.rank = exports["rank_answer"]

    def score(self, q: str, gt: str, ma: str) -> float:
        qb, gb, mb = q.encode("utf-8"), gt.encode("utf-8"), ma.encode("utf-8")
        pq = self.alloc(self.store, len(qb))
        pg = self.alloc(self.store, len(gb))
        pm = self.alloc(self.store, len(mb))
        base = self.memory.data_ptr(self.store)
        mem_len = self.memory.data_len(self.store)
        # data_ptr gives a ctypes pointer; write via the buffer protocol.
        import ctypes
        buf = (ctypes.c_ubyte * mem_len).from_address(ctypes.addressof(base.contents))
        buf[pq:pq + len(qb)] = qb
        buf[pg:pg + len(gb)] = gb
        buf[pm:pm + len(mb)] = mb
        try:
            r = self.rank(self.store, pq, len(qb), pg, len(gb), pm, len(mb))
        finally:
            self.dealloc(self.store, pq, len(qb))
            self.dealloc(self.store, pg, len(gb))
            self.dealloc(self.store, pm, len(mb))
        return float(r)
