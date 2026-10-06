// Phase 4 Part 6 Rust micro-prototype: same workloads as bench/ops_win.py +
// CacheRequest (targeted) vs uncached property paths. Emits one JSON object
// with median/p95 per op (reps via --reps N). Requires a Notepad window for
// the UIA ops (open one beforehand); absent-target path measured with a
// bogus title and a 2s-equivalent bounded wait.
use std::time::Instant;
use windows::{
    Win32::Foundation::*,
    Win32::System::Com::*,
    Win32::UI::Accessibility::*,
    Win32::UI::Input::KeyboardAndMouse::*,
    Win32::UI::WindowsAndMessaging::*,
    core::*,
};

fn ms(d: std::time::Duration) -> f64 {
    d.as_secs_f64() * 1000.0
}

fn pct(mut v: Vec<f64>, p: f64) -> f64 {
    if v.is_empty() {
        return 0.0;
    }
    v.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let k = (v.len() - 1) as f64 * p / 100.0;
    let f = k as usize;
    let c = (f + 1).min(v.len() - 1);
    if f == c {
        return v[f];
    }
    v[f] + (v[c] - v[f]) * (k - f as f64)
}

fn med(v: &[f64]) -> f64 {
    let mut s = v.to_vec();
    s.sort_by(|a, b| a.partial_cmp(b).unwrap());
    if s.is_empty() {
        0.0
    } else {
        s[s.len() / 2]
    }
}

fn wide(s: &str) -> Vec<u16> {
    s.encode_utf16().chain(std::iter::once(0)).collect()
}

fn window_text_len(hwnd: HWND) -> usize {
    unsafe { GetWindowTextLengthW(hwnd) as usize }
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let reps: usize = args
        .iter()
        .position(|a| a == "--reps")
        .and_then(|i| args.get(i + 1))
        .and_then(|s| s.parse().ok())
        .unwrap_or(10);

    // startup_noop baseline
    let mut startup: Vec<f64> = vec![];
    for _ in 0..reps {
        let t = Instant::now();
        let _ = std::hint::black_box(1u64 + 1);
        startup.push(ms(t.elapsed()));
    }

    // window_enumeration
    let mut wenum: Vec<f64> = vec![];
    for _ in 0..reps {
        let t = Instant::now();
        unsafe {
            let mut count = 0usize;
            unsafe extern "system" fn cb(hwnd: HWND, lp: LPARAM) -> BOOL {
                let n = unsafe { &mut *(lp.0 as *mut usize) };
                if unsafe { IsWindowVisible(hwnd) }.as_bool()
                    && unsafe { GetWindowTextLengthW(hwnd) } > 0
                {
                    *n += 1;
                }
                true.into()
            }
            let _ = EnumWindows(Some(cb), LPARAM(&mut count as *mut usize as isize));
            std::hint::black_box(count);
        }
        wenum.push(ms(t.elapsed()));
    }

    // get_foreground
    let mut fg: Vec<f64> = vec![];
    for _ in 0..reps {
        let t = Instant::now();
        unsafe {
            std::hint::black_box(GetForegroundWindow());
        }
        fg.push(ms(t.elapsed()));
    }

    // sendinput_null (zero mouse move)
    let mut sinput: Vec<f64> = vec![];
    for _ in 0..reps {
        let t = Instant::now();
        unsafe {
            let mut input = INPUT {
                r#type: INPUT_MOUSE,
                Anonymous: INPUT_0 {
                    mi: MOUSEINPUT {
                        dx: 0,
                        dy: 0,
                        mouseData: 0,
                        dwFlags: MOUSEEVENTF_MOVE,
                        time: 0,
                        dwExtraInfo: 0,
                    },
                },
            };
            std::hint::black_box(SendInput(&[input], std::mem::size_of::<INPUT>() as i32));
        }
        sinput.push(ms(t.elapsed()));
    }

    // UIA: init once, then ElementFromHandle + Name on Notepad (if present)
    let mut uia_cold: Vec<f64> = vec![];
    let mut uia_name: Vec<f64> = vec![];
    let mut uia_cached: Vec<f64> = vec![];
    let mut uia_setvalue: Vec<f64> = vec![];
    let mut uia_absent: Vec<f64> = vec![];
    unsafe {
        let _ = CoInitializeEx(None, COINIT_MULTITHREADED);
    }
    for _ in 0..reps {
        // FindWindowW (fallible in windows 0.58) + ElementFromHandle path
        let t = Instant::now();
        let title = wide("Untitled - Notepad");
        let hwnd: HWND =
            unsafe { FindWindowW(PCWSTR::null(), PCWSTR(title.as_ptr())) }
                .unwrap_or(HWND(std::ptr::null_mut()));
        let t_find = ms(t.elapsed());
        uia_cold.push(t_find);
        if !hwnd.0.is_null() {
            let t2 = Instant::now();
            unsafe {
                let uia: IUIAutomation =
                    CoCreateInstance(&CUIAutomation as *const _, None, CLSCTX_INPROC_SERVER)
                        .unwrap();
                if let Ok(el) = uia.ElementFromHandle(hwnd) {
                    let t3 = Instant::now();
                    let _name: BSTR = el.CurrentName().unwrap_or_default();
                    uia_name.push(ms(t3.elapsed()));
                    // targeted CacheRequest: Name only (default scope)
                    let t4 = Instant::now();
                    if let Ok(req) = uia.CreateCacheRequest() {
                        let _ = req.AddProperty(UIA_NamePropertyId);
                        if let Ok(el2) =
                            uia.ElementFromHandleBuildCache(hwnd, &req)
                        {
                            let _cn: BSTR = el2.CachedName().unwrap_or_default();
                        }
                    }
                    uia_cached.push(ms(t4.elapsed()));
                    // ValuePattern.SetValue on first Edit descendant (if any)
                    let t5 = Instant::now();
                    if let Ok(cond) = uia.CreatePropertyCondition(
                        UIA_ControlTypePropertyId,
                        &VARIANT::from(50004i32),
                    ) {
                        if let Ok(edit) = el.FindFirst(TreeScope_Descendants, &cond) {
                            if let Ok(pat) =
                                edit.GetCurrentPatternAs::<IUIAutomationValuePattern>(
                                    UIA_ValuePatternId,
                                )
                            {
                                let _ = pat.SetValue(&BSTR::from("rust-probe"));
                            }
                        }
                    }
                    uia_setvalue.push(ms(t5.elapsed()));
                }
            }
            let _ = t2;
        }
        // absent target: bounded ~2s wait equivalent (FindWindowW loop, no UIA timeout)
        let t6 = Instant::now();
        let deadline = t6 + std::time::Duration::from_millis(2000);
        loop {
            let miss = wide("NoSuchAppXYZ123");
            let h: HWND = unsafe { FindWindowW(PCWSTR::null(), PCWSTR(miss.as_ptr())) }
                .unwrap_or(HWND(std::ptr::null_mut()));
            if !h.0.is_null() || Instant::now() >= deadline {
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(100));
        }
        uia_absent.push(ms(t6.elapsed()));
    }
    let _ = window_text_len(HWND(std::ptr::null_mut()));

    // mcp dispatch proxy: serde_json round-trip of a receipt-sized struct
    let mut dispatch: Vec<f64> = vec![];
    for _ in 0..reps {
        let t = Instant::now();
        let v = serde_json::json!({"status":"success","duration_ms":421.0,
            "steps_completed":7,"retries":0,"backend":"uia","cache_hits":5,
            "result":{"window":"Notepad"}});
        let s = serde_json::to_string(&v).unwrap();
        let _: serde_json::Value = serde_json::from_str(&s).unwrap();
        dispatch.push(ms(t.elapsed()));
    }

    let out = serde_json::json!({
        "lang": "rust-gnu",
        "reps": reps,
        "startup_noop": {"median_ms": med(&startup), "p95_ms": pct(startup, 95.0)},
        "window_enumeration": {"median_ms": med(&wenum), "p95_ms": pct(wenum, 95.0)},
        "get_foreground": {"median_ms": med(&fg), "p95_ms": pct(fg, 95.0)},
        "sendinput_null": {"median_ms": med(&sinput), "p95_ms": pct(sinput, 95.0)},
        "uia_findwindow": {"median_ms": med(&uia_cold), "p95_ms": pct(uia_cold, 95.0)},
        "uia_name_uncached": {"median_ms": med(&uia_name), "p95_ms": pct(uia_name, 95.0)},
        "uia_name_cached": {"median_ms": med(&uia_cached), "p95_ms": pct(uia_cached, 95.0)},
        "uia_setvalue": {"median_ms": med(&uia_setvalue), "p95_ms": pct(uia_setvalue, 95.0)},
        "uia_absent_2s": {"median_ms": med(&uia_absent), "p95_ms": pct(uia_absent, 95.0)},
        "mcp_dispatch_proxy": {"median_ms": med(&dispatch), "p95_ms": pct(dispatch, 95.0)},
    });
    println!("{}", serde_json::to_string_pretty(&out).unwrap());
}
