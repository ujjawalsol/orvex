# CLR UIA micro-benchmark, honest scope (Phase 3 Part 6).
# In-box .NET Framework System.Windows.Automation on Win11 Notepad:
# window FindFirst (cold/warm), CacheRequest window-name read, top-level enum.
# KNOWN GAPS (measured, causes UNRESOLVED):
# 1. Managed Descendants/Subtree search does NOT resolve the nested Edit
#    ('Text Editor') in this environment, while raw COM UIA (comtypes, same
#    machine) DOES. SetValue therefore has no CLR number here.
# 2. CacheRequest+Activate window-name reads returned empty in-loop (single
#    probes outside loops succeed); treated as harness artifact, not a claim.
# Every lookup asserts non-null (failures counted, not silently measured).
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$ErrorActionPreference = 'Stop'
$np = Start-Process notepad.exe -PassThru
Start-Sleep -Milliseconds 1000

function Measure-Op($name, $n, [scriptblock]$fn) {
    $ts = @()
    $fails = 0
    for ($i = 0; $i -lt $n; $i++) {
        $sw = [Diagnostics.Stopwatch]::StartNew()
        try {
            $r = & $fn
            if ($r -eq $null) { $fails++ }
        } catch { $fails++ }
        $sw.Stop()
        $ts += $sw.Elapsed.TotalMilliseconds
    }
    $s = $ts | Sort-Object
    $med = $s[[int]($s.Count / 2)]
    $p95 = $s[[int][Math]::Min($s.Count - 1, $s.Count * 0.95)]
    [pscustomobject]@{op=$name; n=$n; failures=$fails;
        median_ms=[Math]::Round($med,2); p95_ms=[Math]::Round($p95,2)}
}

$root = [Windows.Automation.AutomationElement]::RootElement
$nameCond = New-Object Windows.Automation.PropertyCondition(
    [Windows.Automation.AutomationElement]::NameProperty, 'Untitled - Notepad')

$r1 = Measure-Op 'clr_findfirst_cold' 10 {
    $root.FindFirst([Windows.Automation.TreeScope]::Children, $nameCond)
}
$dlg = $root.FindFirst([Windows.Automation.TreeScope]::Children, $nameCond)
if ($dlg -eq $null) { Write-Output 'FATAL: window not found'; exit 1 }
$r2 = Measure-Op 'clr_findfirst_warm' 10 {
    $root.FindFirst([Windows.Automation.TreeScope]::Children, $nameCond)
}
$cacheReq = New-Object Windows.Automation.CacheRequest
$cacheReq.Add([Windows.Automation.AutomationElement]::NameProperty)
$cacheReq.TreeScope = [Windows.Automation.TreeScope]::Element
$r3 = Measure-Op 'clr_cached_window_name' 20 {
    $scope = $cacheReq.Activate()
    try {
        $w = $root.FindFirst([Windows.Automation.TreeScope]::Children, $nameCond)
        if ($w -eq $null) { return $null }
        $w.CachedName
    } finally { $scope.Dispose() }
}
$r4 = Measure-Op 'clr_window_enum' 10 {
    ($root.FindAll([Windows.Automation.TreeScope]::Children,
        [Windows.Automation.Condition]::TrueCondition)).Count
}
$editCond = New-Object Windows.Automation.PropertyCondition(
    [Windows.Automation.AutomationElement]::ControlTypeProperty,
    [Windows.Automation.ControlType]::Edit)
$e = $dlg.FindFirst([Windows.Automation.TreeScope]::Descendants, $editCond)
$r5 = [pscustomobject]@{op='clr_descendants_edit'; n=1;
    failures=([int]($e -eq $null)); median_ms='n/a'; p95_ms='n/a'}
$r1, $r2, $r3, $r4, $r5 | Format-Table -AutoSize | Out-String | Write-Output

$np.CloseMainWindow() | Out-Null
Start-Sleep -Milliseconds 800
Write-Output ("notepad_exited=" + $np.HasExited)
