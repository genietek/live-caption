# LiveCaptionsProbe (Phase 1 feasibility check)

A small console tool that checks whether Windows 11 Live Captions text can be read through
Windows UI Automation on a given machine. It only reads. It never types, pastes, records audio,
or sends anything over the network.

## What it checks

1. Windows build, .NET version, elevation, and whether `LiveCaptions.exe` is running.
2. Finds the Live Captions window by its UIA class name `LiveCaptionsDesktopWindow`
   (falls back to "any top-level window owned by the LiveCaptions process").
3. Prints the window's UI Automation tree, like Inspect.exe would show.
4. Finds the caption element by AutomationId `CaptionsTextBlock`
   (falls back to the longest Text element) and lists its control patterns.
5. Reads the text via the `Name` property and, if supported, `TextPattern`.
6. Subscribes to Name-changed, TextChanged and StructureChanged events and counts them,
   while polling the `Name` property every 200 ms as a baseline.
7. Prints only new candidate text, labelled `NEW`, `REVISED`, `CLEARED` or `RESET/UNMATCHED`.
   This is a naive check. The real incremental engine is Phase 2.
8. Reconnects if Live Captions is closed and reopened.
9. On Ctrl+C prints measured read latency, event coverage, CPU time and peak memory.

## Run it

Option A, prebuilt exe:

- `LiveCaptionsProbe.exe` (self-contained, ~70 MB) runs without installing anything.
- The small framework-dependent exe needs the
  [.NET 8 Desktop Runtime (x64)](https://dotnet.microsoft.com/download/dotnet/8.0).

Windows SmartScreen may warn because the exe is unsigned ("More info" > "Run anyway").

Option B, from source (needs the .NET 8 SDK):

```powershell
cd diagnostics\LiveCaptionsProbe
dotnet run -c Release
```

## Test steps

1. Turn on Live Captions with **Win + Ctrl + L** (first run asks to download speech files).
2. Run `LiveCaptionsProbe.exe` from a normal (not admin) terminal.
3. Play some English speech (a YouTube video is fine) for about 30 seconds.
4. While it runs, close Live Captions and reopen it with Win + Ctrl + L, to test recovery.
5. If you can, switch the caption language to Spanish and play some Spanish speech.
6. Press **Ctrl+C**. The full output is also saved to `diag-output-<time>.txt` in the current folder.

Options: `--interval <ms>`, `--seconds <n>`, `--raw` (print full caption text on every change),
`--tree` (discovery and tree only), `--no-log`.

## Build

```bash
dotnet build diagnostics/LiveCaptionsProbe -c Release
dotnet publish diagnostics/LiveCaptionsProbe -c Release -r win-x64 --self-contained true -p:PublishSingleFile=true
```

It builds on Linux too (`EnableWindowsTargeting`), but only runs on Windows.
