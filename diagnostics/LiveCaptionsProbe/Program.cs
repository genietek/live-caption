// Phase 1 feasibility probe for reading Windows 11 Live Captions through UI Automation.
//
// What it does:
//   1. Prints Windows / Live Captions version info.
//   2. Finds the Live Captions window and dumps its UI Automation tree (like Inspect.exe).
//   3. Finds the caption text element and reads it via the Name property and TextPattern.
//   4. Subscribes to UIA events to see which ones fire, and polls as a baseline.
//   5. Prints only new candidate text using a naive overlap check (the real engine is Phase 2).
//   6. Recovers when Live Captions is closed and reopened.
//   7. Prints timing, CPU and memory numbers on exit (Ctrl+C).
//
// It only reads. It never types, pastes, or sends anything anywhere.

using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Security.Principal;
using System.Text;
using System.Windows.Automation;
using Microsoft.Win32;

namespace LiveCaptionsProbe;

internal static class Program
{
    private const string WindowClassName = "LiveCaptionsDesktopWindow";
    private const string CaptionAutomationId = "CaptionsTextBlock";
    private const string ProcessName = "LiveCaptions";

    private static Log _log = null!;
    private static readonly Stopwatch Clock = Stopwatch.StartNew();

    // Event counters (written from UIA event threads).
    private static long _nameEvents;
    private static long _textChangedEvents;
    private static long _structureEvents;
    private static long _lastNameEventTicks; // Clock ticks of the most recent Name change event

    [MTAThread]
    private static int Main(string[] args)
    {
        var opt = Options.Parse(args);
        if (opt == null) return 2;

        Console.OutputEncoding = new UTF8Encoding(false);
        _log = new Log(opt.LogPath);

        using var cts = new CancellationTokenSource();
        Console.CancelKeyPress += (_, e) => { e.Cancel = true; cts.Cancel(); };
        if (opt.Seconds > 0) cts.CancelAfter(TimeSpan.FromSeconds(opt.Seconds));

        try
        {
            _log.Line("=== LiveCaptionsProbe (Phase 1 feasibility check) ===");
            _log.Line("Privacy: this output contains caption text. Review it before sharing.");
            if (opt.LogPath != null) _log.Line($"Log file: {Path.GetFullPath(opt.LogPath)}");
            _log.Line($"Options: interval={opt.IntervalMs}ms seconds={(opt.Seconds > 0 ? opt.Seconds.ToString() : "until Ctrl+C")} raw={opt.Raw} treeOnly={opt.TreeOnly}");
            _log.Line("");
            PrintEnvironment();
            return Run(opt, cts.Token);
        }
        catch (Exception ex)
        {
            _log.Line($"FATAL: {ex.GetType().FullName}: {ex.Message}");
            _log.Line(ex.ToString());
            return 1;
        }
        finally
        {
            try { Automation.RemoveAllEventHandlers(); } catch { /* best effort */ }
            _log.Dispose();
        }
    }

    // ------------------------------------------------------------------ main flow

    private static int Run(Options opt, CancellationToken ct)
    {
        var stats = new Stats();
        string? lastText = null;
        bool firstAttach = true;

        while (!ct.IsCancellationRequested)
        {
            // ---- discovery (with waiting) ----
            var target = WaitForCaptions(ct, firstAttach, stats);
            if (target == null) break;

            if (firstAttach)
            {
                DumpTree(target.Window);
                DescribeTextElement(target.Text);
                if (opt.TreeOnly) { PrintSummary(stats); return 0; }
            }
            firstAttach = false;

            SubscribeEvents(target);
            _log.Line("");
            _log.Line($"--- Watching captions (poll every {opt.IntervalMs} ms). Speak or play audio. Ctrl+C to stop. ---");

            // ---- polling loop ----
            long lastPollTicks = Clock.ElapsedTicks;
            bool stale = false;
            while (!ct.IsCancellationRequested)
            {
                string text;
                long t0 = Clock.ElapsedTicks;
                try
                {
                    text = target.Text.Current.Name ?? string.Empty;
                }
                catch (Exception ex) when (IsStale(ex))
                {
                    _log.Line($"[{Ts()}] Caption element became unavailable ({ex.GetType().Name}). Reconnecting...");
                    stats.Recoveries++;
                    stale = true;
                    break;
                }
                long readTicks = Clock.ElapsedTicks - t0;
                stats.RecordRead(readTicks);

                if (!string.Equals(text, lastText, StringComparison.Ordinal))
                {
                    stats.Changes++;
                    long evt = Interlocked.Read(ref _lastNameEventTicks);
                    if (evt > lastPollTicks)
                    {
                        stats.ChangesWithPriorNameEvent++;
                        stats.EventLeadTicksTotal += Clock.ElapsedTicks - evt;
                    }
                    ReportChange(lastText, text, opt.Raw);
                    lastText = text;
                }
                lastPollTicks = Clock.ElapsedTicks;

                if (ct.WaitHandle.WaitOne(opt.IntervalMs)) break;
            }

            try { Automation.RemoveAllEventHandlers(); } catch { /* best effort */ }
            if (!stale) break;
            lastText = null; // new caption session after reconnect
        }

        PrintSummary(stats);
        return 0;
    }

    private sealed record Target(AutomationElement Window, AutomationElement Text, int ProcessId);

    private static Target? WaitForCaptions(CancellationToken ct, bool verbose, Stats stats)
    {
        bool announcedWaiting = false;
        while (!ct.IsCancellationRequested)
        {
            var sw = Stopwatch.StartNew();
            var t = TryFindCaptions(verbose);
            sw.Stop();
            if (t != null)
            {
                stats.DiscoveryMs.Add(sw.Elapsed.TotalMilliseconds);
                _log.Line($"[{Ts()}] Connected to Live Captions (pid {t.ProcessId}). Discovery took {sw.Elapsed.TotalMilliseconds:F1} ms.");
                return t;
            }
            if (!announcedWaiting)
            {
                _log.Line($"[{Ts()}] Live Captions window or caption element not found. Start Live Captions (Win+Ctrl+L). Waiting...");
                announcedWaiting = true;
            }
            verbose = false; // only print discovery details once
            if (ct.WaitHandle.WaitOne(1000)) break;
        }
        return null;
    }

    private static Target? TryFindCaptions(bool verbose)
    {
        var procs = Process.GetProcessesByName(ProcessName);
        if (verbose)
        {
            _log.Line($"## Process check: {procs.Length} '{ProcessName}' process(es) running");
            foreach (var p in procs)
            {
                string path = "?", ver = "?";
                try { path = p.MainModule?.FileName ?? "?"; ver = FileVersionInfo.GetVersionInfo(path).FileVersion ?? "?"; }
                catch (Exception ex) { path = $"(unavailable: {ex.GetType().Name})"; }
                _log.Line($"   pid={p.Id} session={p.SessionId} path={path} version={ver}");
            }
        }

        // Strategy 1: top-level window by class name (preferred, not title-based).
        AutomationElement? window = null;
        string strategy = "";
        try
        {
            window = AutomationElement.RootElement.FindFirst(TreeScope.Children,
                new PropertyCondition(AutomationElement.ClassNameProperty, WindowClassName));
            if (window != null) strategy = $"ClassName == {WindowClassName}";
        }
        catch (Exception ex) when (IsStale(ex)) { }

        // Strategy 2: any top-level window owned by a LiveCaptions process.
        if (window == null && procs.Length > 0)
        {
            foreach (var p in procs)
            {
                try
                {
                    var wins = AutomationElement.RootElement.FindAll(TreeScope.Children,
                        new PropertyCondition(AutomationElement.ProcessIdProperty, p.Id));
                    if (verbose)
                        _log.Line($"   Top-level windows for pid {p.Id}: {wins.Count}");
                    foreach (AutomationElement w in wins)
                    {
                        if (verbose) _log.Line($"     - {Describe(w)}");
                        window ??= w;
                    }
                }
                catch (Exception ex) when (IsStale(ex)) { }
            }
            if (window != null) strategy = "ProcessId match (class name did NOT match)";
        }

        if (window == null)
        {
            if (verbose) _log.Line("## Window: NOT FOUND");
            return null;
        }

        if (verbose)
        {
            _log.Line($"## Window found via {strategy}");
            _log.Line($"   {Describe(window)}");
        }

        // Caption text element: AutomationId first, then the longest Text element as a fallback.
        AutomationElement? text = null;
        try
        {
            text = window.FindFirst(TreeScope.Descendants,
                new PropertyCondition(AutomationElement.AutomationIdProperty, CaptionAutomationId));
            if (text != null && verbose) _log.Line($"## Caption element found via AutomationId == {CaptionAutomationId}");

            if (text == null)
            {
                var texts = window.FindAll(TreeScope.Descendants,
                    new PropertyCondition(AutomationElement.ControlTypeProperty, ControlType.Text));
                int best = -1;
                foreach (AutomationElement e in texts)
                {
                    int len = (e.Current.Name ?? "").Length;
                    if (len > best) { best = len; text = e; }
                }
                if (text != null && verbose)
                    _log.Line($"## Caption element NOT found by AutomationId. Fallback picked longest Text element of {texts.Count}: {Describe(text)}");
            }
        }
        catch (Exception ex) when (IsStale(ex))
        {
            if (verbose) _log.Line($"## Caption element search failed: {ex.GetType().Name}: {ex.Message}");
            return null;
        }

        if (text == null)
        {
            if (verbose)
            {
                _log.Line("## Caption element: NOT FOUND. Tree dump follows for investigation.");
                DumpTree(window);
            }
            return null;
        }

        int pid = 0;
        try { pid = window.Current.ProcessId; } catch (Exception ex) when (IsStale(ex)) { }
        return new Target(window, text, pid);
    }

    // ------------------------------------------------------------------ inspection

    private static void DescribeTextElement(AutomationElement el)
    {
        _log.Line("");
        _log.Line("## Caption element details");
        try
        {
            var c = el.Current;
            _log.Line($"   ControlType={c.ControlType.ProgrammaticName} Class='{c.ClassName}' AutomationId='{c.AutomationId}' Framework='{c.FrameworkId}'");
            _log.Line($"   IsOffscreen={c.IsOffscreen} IsEnabled={c.IsEnabled} Rect={c.BoundingRectangle}");
            _log.Line($"   Supported patterns: {PatternList(el)}");
            string name = c.Name ?? "";
            _log.Line($"   Name property: {name.Length} chars: \"{Escape(Trunc(name, 300))}\"");

            if (el.TryGetCurrentPattern(TextPattern.Pattern, out object tpObj))
            {
                var tp = (TextPattern)tpObj;
                var sw = Stopwatch.StartNew();
                string doc = tp.DocumentRange.GetText(-1);
                sw.Stop();
                _log.Line($"   TextPattern text: {doc.Length} chars in {sw.Elapsed.TotalMilliseconds:F2} ms: \"{Escape(Trunc(doc, 300))}\"");
                _log.Line($"   Name == TextPattern text: {string.Equals(name, doc, StringComparison.Ordinal)}");
            }
            else
            {
                _log.Line("   TextPattern: not supported (Name property is the only text source)");
            }

            if (el.TryGetCurrentPattern(ValuePattern.Pattern, out object vpObj))
                _log.Line($"   ValuePattern value: \"{Escape(Trunc(((ValuePattern)vpObj).Current.Value ?? "", 300))}\"");
        }
        catch (Exception ex) when (IsStale(ex))
        {
            _log.Line($"   (element went stale while describing: {ex.GetType().Name})");
        }
    }

    private static void DumpTree(AutomationElement root)
    {
        _log.Line("");
        _log.Line("## UI Automation tree of the Live Captions window (raw view, names truncated)");
        int count = 0;
        void Walk(AutomationElement e, int depth)
        {
            if (count >= 300 || depth > 15) return;
            count++;
            _log.Line($"   {new string(' ', depth * 2)}{Describe(e)} patterns=[{PatternList(e)}]");
            AutomationElement? child;
            try { child = TreeWalker.RawViewWalker.GetFirstChild(e); }
            catch (Exception ex) when (IsStale(ex)) { return; }
            while (child != null)
            {
                Walk(child, depth + 1);
                try { child = TreeWalker.RawViewWalker.GetNextSibling(child); }
                catch (Exception ex) when (IsStale(ex)) { return; }
            }
        }
        Walk(root, 0);
        if (count >= 300) _log.Line("   (tree truncated at 300 nodes)");
    }

    private static string Describe(AutomationElement e)
    {
        try
        {
            var c = e.Current;
            return $"{c.ControlType.ProgrammaticName.Replace("ControlType.", "")} class='{c.ClassName}' id='{c.AutomationId}' fw='{c.FrameworkId}' name=\"{Escape(Trunc(c.Name ?? "", 60))}\"";
        }
        catch (Exception ex) when (IsStale(ex)) { return $"(stale: {ex.GetType().Name})"; }
    }

    private static string PatternList(AutomationElement e)
    {
        try { return string.Join(",", e.GetSupportedPatterns().Select(p => p.ProgrammaticName.Replace("PatternIdentifiers.Pattern", ""))); }
        catch (Exception ex) when (IsStale(ex)) { return "?"; }
    }

    // ------------------------------------------------------------------ events

    private static void SubscribeEvents(Target t)
    {
        try
        {
            Automation.AddAutomationPropertyChangedEventHandler(t.Text, TreeScope.Element,
                (_, _) => { Interlocked.Increment(ref _nameEvents); Interlocked.Exchange(ref _lastNameEventTicks, Clock.ElapsedTicks); },
                AutomationElement.NameProperty);
            _log.Line($"[{Ts()}] Subscribed: Name PropertyChanged on caption element");
        }
        catch (Exception ex) { _log.Line($"[{Ts()}] Could not subscribe Name PropertyChanged: {ex.GetType().Name}: {ex.Message}"); }

        try
        {
            if (t.Text.TryGetCurrentPattern(TextPattern.Pattern, out _))
            {
                Automation.AddAutomationEventHandler(TextPattern.TextChangedEvent, t.Text, TreeScope.Element,
                    (_, _) => Interlocked.Increment(ref _textChangedEvents));
                _log.Line($"[{Ts()}] Subscribed: TextPattern.TextChanged on caption element");
            }
        }
        catch (Exception ex) { _log.Line($"[{Ts()}] Could not subscribe TextChanged: {ex.GetType().Name}: {ex.Message}"); }

        try
        {
            Automation.AddStructureChangedEventHandler(t.Window, TreeScope.Subtree,
                (_, _) => Interlocked.Increment(ref _structureEvents));
            _log.Line($"[{Ts()}] Subscribed: StructureChanged on window subtree");
        }
        catch (Exception ex) { _log.Line($"[{Ts()}] Could not subscribe StructureChanged: {ex.GetType().Name}: {ex.Message}"); }
    }

    // ------------------------------------------------------------------ naive delta (Phase 2 replaces this)

    private static void ReportChange(string? prev, string cur, bool raw)
    {
        if (raw) _log.Line($"[{Ts()}] RAW ({cur.Length}): \"{Escape(cur)}\"");

        if (prev == null)
        {
            _log.Line($"[{Ts()}] INITIAL ({cur.Length} chars): \"{Escape(Trunc(cur, 200))}\"");
            return;
        }
        if (cur.Length == 0)
        {
            _log.Line($"[{Ts()}] CLEARED (caption text is empty)");
            return;
        }
        if (cur.StartsWith(prev, StringComparison.Ordinal))
        {
            _log.Line($"[{Ts()}] NEW: \"{Escape(cur[prev.Length..])}\"");
            return;
        }

        // Sliding window: the end of prev overlaps the start of cur.
        int overlap = LongestSuffixPrefixOverlap(prev, cur);
        int common = CommonPrefix(prev, cur);
        if (overlap >= 12 && overlap > common)
        {
            _log.Line($"[{Ts()}] NEW (window scrolled, {prev.Length - overlap} chars dropped off top): \"{Escape(cur[overlap..])}\"");
            return;
        }

        if (common > 0)
        {
            string oldTail = prev[common..];
            string newTail = cur[common..];
            _log.Line($"[{Ts()}] REVISED: \"{Escape(Trunc(oldTail, 120))}\" -> \"{Escape(Trunc(newTail, 120))}\"");
            return;
        }

        _log.Line($"[{Ts()}] RESET/UNMATCHED (no overlap with previous). Now: \"{Escape(Trunc(cur, 200))}\"");
    }

    private static int LongestSuffixPrefixOverlap(string a, string b)
    {
        int max = Math.Min(a.Length, b.Length);
        for (int k = max; k > 0; k--)
        {
            if (string.CompareOrdinal(a, a.Length - k, b, 0, k) == 0) return k;
        }
        return 0;
    }

    private static int CommonPrefix(string a, string b)
    {
        int n = Math.Min(a.Length, b.Length), i = 0;
        while (i < n && a[i] == b[i]) i++;
        return i;
    }

    // ------------------------------------------------------------------ environment and summary

    private static void PrintEnvironment()
    {
        _log.Line("## Environment");
        try
        {
            using var k = Registry.LocalMachine.OpenSubKey(@"SOFTWARE\Microsoft\Windows NT\CurrentVersion");
            _log.Line($"   Windows: {k?.GetValue("ProductName")} {k?.GetValue("DisplayVersion")} build {k?.GetValue("CurrentBuild")}.{k?.GetValue("UBR")} (note: ProductName often says 'Windows 10' on Windows 11)");
        }
        catch (Exception ex) { _log.Line($"   Windows version: unavailable ({ex.GetType().Name})"); }
        _log.Line($"   OSVersion: {Environment.OSVersion.VersionString}");
        _log.Line($"   .NET: {RuntimeInformation.FrameworkDescription} {RuntimeInformation.ProcessArchitecture}");
        _log.Line($"   UI culture: {System.Globalization.CultureInfo.CurrentUICulture.Name} (InvariantGlobalization may show empty)");
        try
        {
            using var id = WindowsIdentity.GetCurrent();
            bool admin = new WindowsPrincipal(id).IsInRole(WindowsBuiltInRole.Administrator);
            _log.Line($"   Running elevated: {admin}");
        }
        catch (Exception ex) { _log.Line($"   Elevation check failed: {ex.GetType().Name}"); }
        _log.Line($"   Apartment: {Thread.CurrentThread.GetApartmentState()}");
        _log.Line("");
    }

    private static void PrintSummary(Stats s)
    {
        var me = Process.GetCurrentProcess();
        me.Refresh();
        double wall = Clock.Elapsed.TotalSeconds;
        double cpu = me.TotalProcessorTime.TotalSeconds;
        double tickMs = 1000.0 / Stopwatch.Frequency;

        _log.Line("");
        _log.Line("## Summary (measured on this machine)");
        _log.Line($"   Run time: {wall:F1} s");
        if (s.DiscoveryMs.Count > 0)
            _log.Line($"   Discovery: {s.DiscoveryMs.Count} time(s), first {s.DiscoveryMs[0]:F1} ms, max {s.DiscoveryMs.Max():F1} ms");
        _log.Line($"   Recoveries after stale element: {s.Recoveries}");
        if (s.Reads > 0)
            _log.Line($"   Name reads: {s.Reads}, avg {s.ReadTicksTotal * tickMs / s.Reads:F2} ms, max {s.ReadTicksMax * tickMs:F2} ms");
        _log.Line($"   Text changes seen by polling: {s.Changes}");
        _log.Line($"   Events: NamePropertyChanged={Interlocked.Read(ref _nameEvents)} TextChanged={Interlocked.Read(ref _textChangedEvents)} StructureChanged={Interlocked.Read(ref _structureEvents)}");
        if (s.Changes > 1)
        {
            double coverage = 100.0 * s.ChangesWithPriorNameEvent / (s.Changes - 1);
            _log.Line($"   Changes preceded by a Name event: {s.ChangesWithPriorNameEvent}/{s.Changes - 1} ({coverage:F0}%)");
            if (s.ChangesWithPriorNameEvent > 0)
                _log.Line($"   Avg time from Name event to poll detection: {s.EventLeadTicksTotal * tickMs / s.ChangesWithPriorNameEvent:F1} ms");
        }
        _log.Line($"   CPU time: {cpu:F2} s ({(wall > 0 ? 100.0 * cpu / wall / Environment.ProcessorCount : 0):F2}% of all {Environment.ProcessorCount} logical cores)");
        _log.Line($"   Peak working set: {me.PeakWorkingSet64 / 1024.0 / 1024.0:F1} MB, private bytes now: {me.PrivateMemorySize64 / 1024.0 / 1024.0:F1} MB");
    }

    private sealed class Stats
    {
        public readonly List<double> DiscoveryMs = new();
        public int Recoveries;
        public long Reads, ReadTicksTotal, ReadTicksMax;
        public long Changes, ChangesWithPriorNameEvent, EventLeadTicksTotal;

        public void RecordRead(long ticks)
        {
            Reads++;
            ReadTicksTotal += ticks;
            if (ticks > ReadTicksMax) ReadTicksMax = ticks;
        }
    }

    // ------------------------------------------------------------------ helpers

    private static bool IsStale(Exception ex) =>
        ex is ElementNotAvailableException or COMException or InvalidOperationException or TimeoutException;

    private static string Ts() => Clock.Elapsed.TotalSeconds.ToString("F3", System.Globalization.CultureInfo.InvariantCulture) + "s";

    private static string Trunc(string s, int max) => s.Length <= max ? s : s[..max] + "…";

    private static string Escape(string s) => s.Replace("\r", "\\r").Replace("\n", "\\n");
}

internal sealed class Options
{
    public int IntervalMs = 200;
    public int Seconds;
    public bool Raw;
    public bool TreeOnly;
    public string? LogPath = $"diag-output-{DateTime.Now:yyyyMMdd-HHmmss}.txt";

    public static Options? Parse(string[] args)
    {
        var o = new Options();
        for (int i = 0; i < args.Length; i++)
        {
            switch (args[i])
            {
                case "--interval" when i + 1 < args.Length && int.TryParse(args[i + 1], out var ms) && ms >= 20:
                    o.IntervalMs = ms; i++; break;
                case "--seconds" when i + 1 < args.Length && int.TryParse(args[i + 1], out var s) && s >= 0:
                    o.Seconds = s; i++; break;
                case "--raw": o.Raw = true; break;
                case "--tree": o.TreeOnly = true; break;
                case "--no-log": o.LogPath = null; break;
                default:
                    Console.Error.WriteLine("""
                        Usage: LiveCaptionsProbe [--interval <ms>=200] [--seconds <n>] [--raw] [--tree] [--no-log]
                          --interval  poll interval in ms (min 20)
                          --seconds   stop after n seconds (default: run until Ctrl+C)
                          --raw       also print the full caption text on every change
                          --tree      print discovery info and the UIA tree, then exit
                          --no-log    do not write diag-output-*.txt in the current folder
                        """);
                    return null;
            }
        }
        return o;
    }
}

internal sealed class Log : IDisposable
{
    private readonly object _gate = new();
    private readonly StreamWriter? _file;

    public Log(string? path)
    {
        if (path == null) return;
        try { _file = new StreamWriter(path, false, new UTF8Encoding(false)) { AutoFlush = true }; }
        catch (Exception ex) { Console.Error.WriteLine($"(could not open log file {path}: {ex.Message})"); }
    }

    public void Line(string s)
    {
        lock (_gate)
        {
            Console.WriteLine(s);
            _file?.WriteLine(s);
        }
    }

    public void Dispose()
    {
        lock (_gate) _file?.Dispose();
    }
}
