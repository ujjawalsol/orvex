// Phase 0 C# prototype — same ops as bench/ops_win.py.
// STATUS: source-skeleton, build PENDING (no .NET SDK on bench machine).
// Each op must emit JSONL samples identical to bench/harness.py schema and a
// summary with median/p95/p99. Ops: startup_noop, window_enumeration,
// get_foreground, sendinput_null, clipboard_roundtrip, uia_cold_lookup,
// uia_warm_lookup, screenshot, mcp_dispatch_echo.
// UIA via System.Windows.Automation with CacheRequest (targeted scope).

using System;
using System.Diagnostics;

Console.Error.WriteLine("csharp-bench: .NET SDK not installed; source ready for build.");
var sw = Stopwatch.StartNew();
GC.KeepAlive("startup_noop");
sw.Stop();
Console.Error.WriteLine($"startup_noop_ms={sw.Elapsed.TotalMilliseconds:F4}");
