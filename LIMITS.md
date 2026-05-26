# Limits & Constraints

Deep research on every limit affecting long-running CUA data collection on macOS.

---

## 1. CGEventTap (Input Monitoring)

### Callback Timeout
macOS disables the event tap if the callback takes too long to process an event. The exact timeout is undocumented, but empirical evidence (Ghostty, Karabiner) suggests **~10-15 seconds**. The tap receives `kCGEventTapDisabledByTimeout` (0xFFFFFFFE).

**Impact**: Input events silently stop forever. The tap must be explicitly re-enabled with `CGEventTapEnable()`.

**Status**: ❌ Not handled. Once disabled, events stop until process restart.

### Sleep/Wake
Event taps become invalid after sleep/wake, screen lock/unlock, and fast user switch. The tap must be re-created.

**Impact**: After wake from sleep, events stop until process restart.

**Status**: ❌ Not handled.

### Secure Input
Password fields, `sudo` prompts, and the login screen suppress ALL events from reaching any event tap. This is by design (Apple security).

**Impact**: Events stop during password entry. No mitigation possible.

**Status**: ⚠️ Cannot mitigate. Must be designed for.

### kCGHIDEventTap vs kCGSessionEventTap
`kCGHIDEventTap` captures events at the HID driver level (earliest possible). `kCGSessionEventTap` captures at the login session level. We use `kCGHIDEventTap`.

Key difference: `kCGHIDEventTap` provides the most "real" capture but requires Input Monitoring permission. Both are equally susceptible to timeout, sleep/wake, and Secure Input.

### Sequoia Timestamp Requirement
macOS Sequoia (15.x) requires posted events to carry `CLOCK_UPTIME_RAW` timestamps via `CGEventSetTimestamp()`. Without them, posted events are silently dropped. This only affects event *posting* (injection), not monitoring.

**Impact**: None for our read-only monitoring.

**Status**: ✅ Not applicable (we only monitor).

### TCC Silent Death
If the binary is re-signed (recompiled, code signature changes), TCC may return a cached "trusted" result for the new binary identity, but the old identity's permissions don't carry over. Result: `CGEventTapCreate()` returns a non-nil tap that silently delivers zero events.

**Impact**: Complete monitoring failure with no error message.

**Status**: ❌ Not handled.

---

## 2. AX API (Accessibility Tree)

### IPC Overhead
Every AX API call is a synchronous Mach IPC (MIG) call to the target process. Measuring ~32 calls/sec from a single process saturates the main thread's event dispatch (cmux project data). The bottleneck is IPC round-trip time, not an OS-enforced rate limit.

**Impact**: High-frequency polling causes system UI lag.

**Status**: ✅ Mitigated — AX capture runs on a background thread at ~1fps, not 32/sec.

### Default Messaging Timeout: 6 Seconds
Each AX API call blocks on `mach_msg` waiting for the target process to respond. Apple's default timeout is **6 seconds** (`AXUIElementSetMessagingTimeout`). If the target app is frozen, the call blocks for the full 6 seconds. Can be set as low as ~0.001s.

**Impact**: A single frozen app can block AX tree traversal for 6 seconds, stalling the capture loop.

**Status**: ❌ Not handled. No custom timeout set.

### Unresponsive App Hanging
When an app is stuck (e.g., main thread blocked by debugger breakpoint, infinite loop, or swap thrashing), AX calls block on `mach_msg` until timeout. This is the most widely reported AX reliability issue (yabai, alt-tab, Hammerspoon all document it).

**Impact**: Our 1fps a11y capture thread can hang for 6 seconds, then catch up. No data loss but gaps in tree updates.

**Status**: ❌ Not handled (6s default timeout).

### Electron App Tree Size
Electron apps (Slack, Discord, VS Code, Notion) produce extremely large, sparse accessibility trees. A Slack window can expose **3000+ nodes**, with traversal times of **400-800ms**. The tree is mostly empty `AXGroup` nodes with no `AXTitle` or `AXValue` on interactive elements.

**Impact**: Tree captures of Electron apps are slow and mostly useless data.

**Status**: ✅ Partially mitigated — depth limit of 5. Electron trees may still be larger than expected because the first few levels contain many empty containers.

### kAXErrorCannotComplete
Returned for three distinct conditions: (1) stale TCC cache (permission broken), (2) target app never implemented NSAccessibility (Qt, OpenGL, Electron without `--force-renderer-accessibility`), (3) target app is alive but temporarily unresponsive (first ~200ms of launch).

**Impact**: Transient failures cause false positives. Permanent failures waste retry cycles.

**Status**: ❌ Cannot disambiguate. All failures treated the same.

### Stale TCC Cache (Sequoia)
`AXIsProcessTrusted()` returns cached results that can go stale after OS updates or app re-signs. The function returns `true` but real AX calls return `kAXErrorAPIDisabled`. Confirmed present in macOS 15 (Sequoia) and macOS 26 (Tahoe). No public API to invalidate. Only fix: restart the process.

**Impact**: Complete AX failure with misleading "trusted" status.

**Status**: ❌ Not detected. Will silently log errors without identifying the root cause.

### Observers vs Polling
The AX notification API (`AXObserverCreate`) has known bugs around process restart and thread explosion. Fazm, Karabiner, and alt-tab all independently chose polling over notifications for reliability.

**Impact**: Our polling approach (1fps) is the correct design choice.

**Status**: ✅ Correct approach.

---

## 3. CUA Training Pipeline (JSONL Format)

### OpenAI File Size Limit: 512 MB
OpenAI's fine-tuning and batch APIs accept JSONL files up to **512 MB** per file. HuggingFace datasets: no hard limit (uses Arrow memory-mapping). Storage limit: 100 GB per org (classic) or 2.5 TB per project.

**Impact**: A single continuous session at 1fps generates ~18 MB/hour of JSONL. That's ~28 hours before hitting 512 MB.

**Status**: ⚠️ No rotation. Sessions run until stopped manually or by hard limits.

### Image Format: PNG is the Standard
Every major CUA training dataset (AgentNet, OSWorld, WebSTAR, GUI-360, PC Agent-E) uses **PNG** for screenshots. JPEG is rarely used and the compression artifacts are known to degrade model accuracy on fine-grained UI tasks (button edges, text boundaries, pixel-perfect click targets).

**Impact**: JPEG screenshots may produce lower-quality training data.

**Status**: ⚠️ Default is now JPEG (we changed it). PNG is available as opt-in.

### Recommended Resolution: 1024x768 or 1280x720
Anthropic's official recommendation is **1024x768** (4:3 XGA) as the historical baseline, and **1280x720** (16:9) for Claude 4.6+. They state: "pre-downscaling screenshots to API limits is worth more than almost any other optimization." Higher resolution hurts model accuracy and increases latency.

**Impact**: Our 1440px default is higher than recommended. Coordinate values may not align with training pipeline expectations.

**Status**: ⚠️ 1440px is above the recommended range.

### Trajectory Length: 5-25 Steps
Typical CUA training trajectories range from 3-50 steps, with most in the 5-25 step range. Context windows (typically 8K-32K tokens) constrain maximum length. OpenAI's sample app defaults to 24 max turns.

**Impact**: Our tool creates one continuous trajectory per session. A 1-hour session at 1fps produces 3600+ "steps" — orders of magnitude longer than typical training trajectories.

**Status**: ❌ No trajectory segmentation. Continuous sessions produce unrealistic trajectory lengths.

### AX Tree Schema: No Standard
There is no standardized schema for accessibility trees in CUA training data. AgentNet/OpenCUA converts AX trees to natural language descriptions. Anthropic's patent describes using AX metadata as an input layer converted to reasoning annotations. No project uses the raw AX tree format in training.

**Impact**: Our raw AX tree JSON may not be useful to most training pipelines without post-processing.

**Status**: ⚠️ Recorded as raw nested dict. No conversion to text.

### Missing Display Metadata
Training pipelines expect `display_width_px` / `display_height_px` in observations to correctly scale coordinates. Without this, coordinate values in actions may not align with screenshot pixels.

**Impact**: Coordinate mismatch between actions and screenshot content.

**Status**: ❌ Not recorded in observations.

### Sequence ID Ordering
Events are written asynchronously from multiple capture threads. Sequence IDs are assigned atomically at enqueue time, but the writer flushes events in queue order (enqueue order ≈ timestamp order but not guaranteed across threads). Training pipelines typically assume monotonic ordering on disk.

**Impact**: On-disk event order may differ from sequence ID order by a small window (~tens of events during bursts).

**Status**: ❌ No ordering guarantee. Async writes from 4 threads interleave.

### Prompt Injection via Screenshots
Anthropic has documented that content visible on screen can override model instructions in CUA usage. They added a classifier defense. This is a training data quality concern: if screenshots contain content that contradicts or overrides the agent's instructions.

**Impact**: Training data may contain adversarial or confusing content.

**Status**: ⚠️ Inherent to the modality. No mitigation in our tool.

---

## 4. macOS Filesystem (APFS)

### Files Per Directory: Practical Threshold ~50K
APFS uses a B-tree for directory entries, not flat catalog like HFS+. Theoretical max is ~2.1 billion items per directory. Practical performance degradation becomes visible above ~50,000-100,000 items per folder. Finder/`ls` become painfully slow at these counts.

**Impact**: Performance issues for browsing output directories.

**Status**: ✅ Well-handled. 1000 files per subdirectory. Could safely go to 5000.

### Filename Length: 255 Bytes
POSIX `NAME_MAX` is 255 UTF-8 bytes on APFS. For ASCII filenames this is 255 characters. For multi-byte characters (e.g., emoji), fewer. APFS on-disk limit is higher (1022 bytes) but the POSIX system call layer enforces 255.

**Impact**: Our filenames (`000001.png`) are 11 bytes. No risk.

**Status**: ✅ Safe.

### Path Length: 1024 Bytes
`PATH_MAX` is 1024 bytes including null terminator. Kernel error for exceeding: `ENAMETOOLONG`.

**Impact**: Session paths like `~/.cua-collector/sessions/20260526_120000_abc12345/screenshots/000/000001.jpg` are ~100 bytes. No risk for normal use. Risk if `$HOME` is very deep.

**Status**: ✅ Safe for typical configurations.

### Large File Buffer Cache Slowdown: 200x Penalty
macOS's unified buffer cache has a known bug (documented since Big Sur, not fully fixed) where sequential writes to large files can collapse from ~2 GB/s to **~10 MB/s** — a 200x penalty — when the file exceeds available RAM. This occurs for specific write patterns that trigger cache thrashing and kernel lock contention.

**Impact**: A multi-GB JSONL file suddenly becomes 200x slower to write, potentially causing queue backup, event drops, and missed capture intervals.

**Status**: ❌ No file rotation. Single JSONL grows unbounded.

### APFS Small File COW Overhead
APFS uses copy-on-write for all metadata operations. Each small file creation triggers: B-tree insert + directory record write + inode write + COW metadata update. Time Machine snapshots amplify this — each screenshot appears as a new data version in every snapshot.

**Impact**: Higher than expected disk and I/O for screenshot directories. Time Machine backups grow faster.

**Status**: ⚠️ No mitigation. Recommend Time Machine exclusion.

### Sleep: Processes Suspended
When macOS sleeps (lid close, idle timer, menu sleep), user-space processes are suspended. Mach timers pause. On wake, timers do NOT catch up — elapsed sleep time is simply lost. The process resumes where it left off.

**Impact**: Capture pauses during sleep. No data loss, but a gap in the trajectory.

**Status**: ❌ No sleep/wake handlers. Timer-based capture may behave unexpectedly (a 1s timer that slept for 8 hours fires once).

### I/O Throttling for Background Processes
macOS applies I/O throttling to background-priority threads (`IOPOL_THROTTLE`): 500ms window, 200ms sleep per occurrence (25ms for SSDs). Our writer thread may be classified as background.

**Impact**: JSONL writes may be delayed during foreground I/O contention, causing queue backup.

**Status**: ❌ No I/O priority set for writer thread.

### Open File Descriptors: 256 Default
macOS soft limit (`ulimit -n`) defaults to **256** open file descriptors per process. Hard limit is 10,240 (OPEN_MAX). System-wide max (`kern.maxfiles`) defaults to 245,760.

**Impact**: Our tool opens/closes files per write, so peak FDs is low. No risk at current usage pattern. Risk if adding long-lived file handles (e.g., multiple open log files, network connections).

**Status**: ✅ Safe for current pattern. Document `ulimit -n 10240` for future.

### Disk Write Watchdog
macOS tracks sustained writes over a 24-hour rolling window. Threshold is roughly ~1 GB/day. Exceeding it generates diagnostic reports (notifications in Console, not process termination). At extremely high sustained rates (~8.5 GB/23h), process termination is possible but rare.

**Impact**: 1fps JPEG screenshots at ~300 KB each = ~25 GB/day, well above the diagnostic threshold. At 1fps PNG = ~90 GB/day. Diagnostic reports will be generated.

**Status**: ⚠️ Will generate diagnostics at default rate. No termination expected.

---

## 5. PyObjC Runtime

### Autorelease Pools
Essential in long-running loops. Without periodic pool draining, PyObjC CoreFoundation objects accumulate unboundedly. The `objc.autorelease_pool()` context manager is the correct pattern. The old manual `NSAutoreleasePool.alloc().init()` pattern is crash-prone.

**Impact**: Without pools: memory grows until swap death. With pools: stable.

**Status**: ✅ Correctly used in all 3 capture loops.

### Known Leak Fix (PyObjC 12.1)
PyObjC Issue #657: memory leak when creating many NS objects in a loop with `autorelease_pool()` — fixed in PyObjC 12.1 (Jul 2025). Prior versions had unbounded memory growth even with pools.

**Impact**: Versions before 12.1 have a real memory leak for long sessions.

**Status**: ⚠️ Requirements.txt specifies `pyobjc>=10.2`. Should be `>=12.1`.

### Thread Safety
PyObjC 11.1+ is thread-safe. Prior versions had race conditions importing ObjC classes inside threads (`NSArray.alloc().init()` failed concurrently). Fixed in 11.1. Apple's frameworks (NSMutableArray, NSMutableDictionary, AppKit) remain unsafe for concurrent access.

**Impact**: Our multi-thread architecture (screen, a11y, input, window threads) requires thread-safe bridge.

**Status**: ⚠️ Requirements.txt allows versions as old as 10.2, which are NOT thread-safe.

### Per-Call Overhead: ~30-50x vs Native
PyObjC benchmarks (M1, PyObjC 8.0):
- Python function call: 0.027 µs
- `NSObject.description`: 0.975 µs (36x overhead)
- ObjC→Python callback: 8.048 µs per invocation

**Impact**: For our 1fps capture, overhead is negligible. For high-frequency input events (mouse moves at 60hz), each CGEventTap callback has ~8 µs overhead. With mouse-move sampling at 10%, this is fine.

**Status**: ✅ Acceptable for our capture rates.

### SIGINT Not Reliable in CFRunLoop
Python's signal handler only checks the signal flag between bytecode instructions. When blocked in an ObjC method call (like `CFRunLoopRun()` in the event tap), the signal handler won't fire until control returns to Python. This means Ctrl+C may not work during event tap operation.

**Impact**: User may need to force-kill the process. `atexit` handlers (PID file cleanup, writer flush) may not run.

**Status**: ❌ SIGINT handler may not fire when event tap thread is running.

### ObjC-Side Retain Cycles
Python's garbage collector cannot collect reference cycles that involve Objective-C objects. If object A holds a strong ref to object B and B holds a strong ref back, the cycle leaks permanently.

**Impact**: Permanent memory leak if our code creates cycles across the bridge.

**Status**: ✅ Low risk. Our architecture is callback-based, not object-graph-based.

### Thread Creation Limit: ~8192
macOS has a per-process thread limit around 8192 (observed empirically). This is not a concern for our 6 threads (screen, a11y, input, window, writer, watchdog).

**Status**: ✅ Safe.

### `atexit` May Not Run
PyObjC Issue #124: `Py_Finalize` runs while Cocoa is still cleaning up — ObjC code calls back into the already-destroyed Python interpreter, causing crash. This prevents `atexit` handlers from running.

**Impact**: PID file not cleaned, writer may not flush, session_end may not be written.

**Status**: ❌ No mitigation. Relies on `atexit` for cleanup.

---

## 6. macOS System / Process

### App Nap
macOS pauses (sleeps) hidden or inactive applications to save power. App Nap reduces timer precision from ms to seconds or minutes. Introduced in OS X 10.9. Can be disabled programmatically via `NSProcessInfo` activity with `.userInitiated` flag.

**Impact**: If the user switches to another app, our capture may be paused by App Nap. Screenshot interval stretches from 1s to potentially minutes.

**Status**: ❌ No App Nap prevention. Our daemon process may be napped when not the frontmost app.

### Thermal Throttling
Apple Silicon Macs, especially fanless models (MacBook Air), throttle CPU frequency under sustained load. `ProcessInfo.thermalState` reports `nominal` → `fair` → `serious` → `critical`. At `serious`/`critical`, CPU frequency is reduced and background processes get less priority.

**Impact**: Capture interval may stretch under thermal pressure. Tree traversal may take longer.

**Status**: ❌ Not monitored.

### No OOM Killer
Unlike Linux, macOS does NOT have an OOM killer for user-space processes. Under memory pressure, the system compresses memory, then swaps, then slows down. No process is killed for using too much memory.

**Impact**: Memory leaks cause slowdown, not death. Safe for long sessions.

**Status**: ✅ Safe by design.

### Permission Revocation
TCC permissions are stored in SQLite databases at `/Library/Application Support/com.apple.TCC/TCC.db` and per-user `~/Library/...`. They persist across reboots and are not revoked mid-session. The only ways to lose permissions: (1) binary re-signed with different code identity, (2) user manually resets via `tccutil`, (3) OS update clears TCC data.

**Impact**: No mid-session revocation. Safe for 24/7 operation.

**Status**: ✅ Safe.

### Memory Pressure Behavior
macOS has a layered degradation model: (1) memory compression (up to 50% savings), (2) swap to SSD, (3) `memory_pressure` warnings. Yellow zone (~50-30% free): compression active. Red zone (<30% free): heavy swapping, system beachballs. No kill.

**Impact**: Under memory pressure, our process slows down but survives.

**Status**: ⚠️ No memory pressure monitoring.

---

## Summary: Action Items

### Critical (will cause data loss or silent failure)

| # | Issue | Module | Fix |
|---|-------|--------|-----|
| 1 | `kCGEventTapDisabledByTimeout` not handled | `capture/input_monitor.py` | Handle 0xFFFFFFFE event, call `CGEventTapEnable()` |
| 2 | Sleep/wake invalidates event tap | `capture/input_monitor.py` | Register `NSWorkspaceDidWakeNotification` to re-create tap |
| 3 | JSONL file grows unbounded (200x slowdown) | `storage/writer.py` | Rotate at 400 MB |
| 4 | SIGINT unreliable in CFRunLoop | `__main__.py` | Use `installMachInterrupt()` |
| 5 | App Nap pauses capture | `session.py` | `NSProcessInfo.beginActivity(.userInitiated)` |

### High (limits usability or quality)

| # | Issue | Module | Fix |
|---|-------|--------|-----|
| 6 | AX messaging timeout is 6s default | `capture/a11y.py` | `AXUIElementSetMessagingTimeout(element, 2.0)` |
| 7 | TCC staleness undetected (Sequoia) | `capture/a11y.py` | Probe Finder on persistent `kAXErrorCannotComplete` |
| 8 | Sleep/wake not handled (capture gaps) | `session.py` | Register sleep/wake notifications to pause/resume |
| 9 | Output dir not excluded from Time Machine/Spotlight | startup | Add `.metadata_never_index` + Time Machine exclusion |
| 10 | No trajectory segmentation | `session.py` | Add step/split logic (or emit per-step markers) |
| 11 | Display dimensions not recorded | `session.py` | Add `display_width_px`/`display_height_px` to observations |

### Medium (improves defaults)

| # | Issue | Module | Fix |
|---|-------|--------|-----|
| 12 | Default resolution 1440px > recommended 1280x720 | `config.py` | Change default `max_width` to 1280 |
| 13 | Default format is JPEG (should be PNG for training) | `config.py` | Keep JPEG as default for disk, document tradeoff |
| 14 | PyObjC version too old (`>=10.2`) | `requirements.txt` | Bump to `>=12.1` |
| 15 | TCC silent death undetected (event tap) | `capture/input_monitor.py` | Add health check: verify events received within N seconds |

### Low (nice to have)

| # | Issue | Module | Fix |
|---|-------|--------|-----|
| 16 | No thermal state monitoring | `session.py` | Log warning at `serious`/`critical` |
| 17 | No I/O priority set for writer | `storage/writer.py` | Set foreground I/O priority |
| 18 | Disk write diagnostics at 25 GB/day | config | Note in docs; no code fix needed |
| 19 | Coordinate mismatch without display metadata | `models.py` | Add `display_size` to observation factory |

---

## Sources

- Apple Developer Documentation (CGEventTap, AX API, APFS)
- PyObjC GitHub Issues: #342, #359, #411, #569, #576, #603, #609, #619, #627, #630, #642, #657
- Ghostty GitHub Issues: #11390, #11819
- Karabiner-Elements GitHub: #4414, #4418
- alt-tab-macos GitHub: #348, #4669, commit 7ab7c82
- yabai GitHub: #439, #600
- cmux GitHub Issue: #2985
- Fazm technical blog: fazm.ai
- Eclectic Light Co: APFS directories, I/O throttling explainers
- AgentNet dataset card (HuggingFace: xlangai/AgentNet)
- Anthropic Computer Use documentation (2026)
- OpenAI CUA sample app (GitHub)
- PC Agent-E paper (arXiv 2505.13909)
- Daniel Raffel (2026): CGEventTap code signing issues
- Stack Overflow, Apple Developer Forums (multiple threads)
