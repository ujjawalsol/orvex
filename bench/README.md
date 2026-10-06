# ORVEX Benchmark Suite

Reproducible performance benchmarks and measurement results for ORVEX Windows Automation MCP.

## Overview

The benchmark suite verifies the latency, throughput, memory overhead, and safety guarantees of ORVEX against industry standards (Playwright, raw COM, and traditional RPA tools).

## Directory Structure

```text
bench/
├── README.md               # Benchmark suite documentation (this file)
├── harness.py              # Statistical measurement harness (median, p95, p99, stddev)
├── machine.py              # Machine environment metadata collector
├── sta_mta.py              # COM STA vs MTA threading benchmark
├── uia_micro.py            # Microsecond-level UI Automation primitive tests
├── ops_win.py              # Windows OS automation operations benchmark
├── router.py               # Capability router dispatch benchmark
├── browser_bench.py        # Native Chrome/Edge CDP automation benchmark
├── browser_compare.py      # ORVEX native CDP vs Playwright comparison
├── recovery_bench.py       # Session recovery and reconnection benchmarks
├── window_decomp.py        # Window decomposition latency benchmark
├── session_bench.py        # Persistent session handle benchmark
├── setvalue_before_after.py# SetValue optimization verification (0ms waitTime)
├── master_final.py         # Full automated benchmark pipeline
└── results/
    ├── master_final.json   # Aggregated certified benchmark results
    ├── browser_bench.json  # Chrome CDP latency and memory measurements
    ├── recovery_bench.json # Crash recovery and reconnect metrics
    └── final/              # Frozen release certification receipts
```

## Running Benchmarks

Ensure the ORVEX virtual environment is active:

```cmd
.venv\Scripts\python.exe bench\master_final.py
```

To run individual benchmark suites:

```cmd
.venv\Scripts\python.exe bench\browser_bench.py
.venv\Scripts\python.exe bench\sta_mta.py
.venv\Scripts\python.exe bench\ops_win.py
```

## Summary of Certified Performance

- **COM Primitive Latency**: < 2 ms (SetValue / Pattern Invoke)
- **Window Enumeration**: < 0.4 ms
- **Memory Footprint**: ~ 24 MB peak RSS (Zero Chromium/browser binary bundle)
- **Startup Overhead**: < 30 ms
- **Recovery Latency**: < 15 ms on session reconnect
