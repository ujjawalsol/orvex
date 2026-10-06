# Prototype status — Phase 4 (updated with MEASURED Rust numbers)

## Toolchains

- Rust 1.99.0 stable (msvc host + gnu std) + WinLibs gcc 16.2 (project-local,
  `bench/results/winlibs`, NOT system-wide). MSVC BuildTools never materialized;
  gnu path used instead. Build works: `rust-bench` 1.8 MB exe, 11 MB peak RSS.
- .NET SDK: not installed (bounded budget; CLR in-box evidence taken instead).

## Rust measured (gnu exe, --reps 10, Notepad open; medians)

- startup_noop 0.0001 ms / window_enumeration 0.07 / get_foreground 0.0014 /
  sendinput_null 0.33 / uia_findwindow 0.005 / uia_name_uncached 0.38 /
  uia_name_cached 1.22 / **uia_setvalue 6.8** / uia_absent_2s 2015 /
  mcp_dispatch_proxy (serde_json) 0.004 ms.
- Cold process: ~2.09 s wall for --reps 1 (includes the 2 s absent-wait op by
  design) ; Python+uiautomation import alone: 392 ms.

## Python same-workload comparison (medians)

- window_enumeration 0.38 / foreground 0.007 / sendinput 0.33 /
  clipboard 0.13 / screenshot_full 49 / region 16 / mcp echo 0.04-0.06 /
  SetValue **505 (library sleep) -> 1.7 with waitTime=0** (verified write).
- Raw COM SetValue cost is ~2-7 ms in BOTH runtimes; the 500 ms was
  `OPERATION_WAIT_TIME` sleep in the Python wrapper, same class as Invoke/Click.

## CLR (.NET Framework in-box) measured

- Window find cold 5.9 / warm 5.0 / enum 10.7 ms (same order as raw COM 1-4 ms).
- Managed Descendants/Subtree search FAILS on nested Win11 Edit where raw COM
  succeeds (cause unresolved). CLR SetValue unmeasured for this reason.

## Decision input (not a final lock without .NET 8 SDK numbers)

1. UIA-bound op costs are same-order across Rust/raw-COM/CLR (~ms); provider +
   wrapper sleeps dominate, not language.
2. Rust wins: deploy (1.8 MB static, 11 MB RSS, ms startup), no GC pauses,
   no wrapper-sleep class of bugs (explicit calls), windows-rs full coverage.
3. CLR/C# wins: first-party UIA/COM interop, faster dev velocity for new patterns.
4. Engine stays Python (measured: native ops now ms-scale after sleep removal;
   rewrite cost unjustified by data). If a rewrite is ever justified, Rust is
   the evidence-backed candidate (binary proven here), NOT assumed faster.
