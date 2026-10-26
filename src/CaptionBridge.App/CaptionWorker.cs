using System;
using System.Collections.Concurrent;
using System.Diagnostics;
using System.Text;
using System.Threading;
using CaptionBridge.Core;

namespace CaptionBridge;

internal sealed record WorkerStatus
{
    public bool Capturing { get; init; }
    public bool Connected { get; init; }
    public string CaptionStatus { get; init; } = "";
    public string Pending { get; init; } = "";
    public string Transcript { get; init; } = "";
    public bool AutoInsert { get; init; }
    public bool Paused { get; init; }
    public GateStatus Gate { get; init; }
    public string GateReason { get; init; } = "";
    public string Target { get; init; } = "";
    public string? LockedTarget { get; init; }
    public bool LockArmed { get; init; }
    public string LastInsert { get; init; } = "";
    public double AvgReadMs { get; init; }
    public double LastInsertMs { get; init; }
    public double LastDiscoveryMs { get; init; }
    public int Resets { get; init; }
    public int CorrectionsIgnored { get; init; }
    public long InsertedChars { get; init; }
    public int FailedInserts { get; init; }
    public string TargetDetails { get; init; } = "";
    public string CaptionSample { get; init; } = "";
    public long SnapshotChanges { get; init; }
    public long CommittedChars { get; init; }
    public int DroppedChars { get; init; }
}

/// <summary>
/// The background loop: poll Live Captions, run the incremental processor, check focus, insert.
/// Runs on its own MTA thread so UI Automation calls never block the UI. The UI talks to it only through
/// thread-safe commands and receives immutable <see cref="WorkerStatus"/> snapshots.
/// </summary>
internal sealed class CaptionWorker : IDisposable
{
    private const int TranscriptMaxChars = 6000;

    private readonly ConcurrentQueue<Action> _commands = new();
    private readonly AutoResetEvent _wake = new(false);
    private readonly CancellationTokenSource _cts = new();
    private readonly Thread _thread;
    private readonly IClipboardAccess _clipboard;
    private readonly Stopwatch _clock = Stopwatch.StartNew();

    private readonly LiveCaptionsSource _source = new();
    private readonly FocusProbe _focus = new();
    private readonly TextInserter _inserter = new();
    private readonly InsertionGate _gate = new();
    private CaptionProcessor _processor;
    private AppSettings _settings;

    private readonly StringBuilder _transcript = new();
    private bool _capturing;
    private bool _systemPause;
    private string _lastSnapshot = "";
    private TimeSpan _lastSnapshotChange;
    private TimeSpan _nextConnectAttempt;
    private TimeSpan _nextFocusCheck;
    private FocusSnapshot? _lastFocus;
    private string _lastInsert = "";
    private double _lastInsertMs;
    private long _insertedChars;
    private int _failedInserts;
    private long _reads;
    private double _readMsTotal;
    private long _snapshotChanges;
    private long _committedChars;
    private string _lastGateReason = "";
    private string _lastDropReason = "";
    private WorkerStatus? _lastPublished;
    private TimeSpan _lastPublishTime;

    public event Action<WorkerStatus>? StatusChanged;
    public event Action<string>? Message;

    public CaptionWorker(AppSettings settings, IClipboardAccess clipboard)
    {
        _settings = settings;
        _clipboard = clipboard;
        _processor = new CaptionProcessor(ToProcessorOptions(settings));
        _gate.AutoPauseOnFocusLoss = settings.AutoPauseOnFocusLoss;
        _thread = new Thread(Run) { IsBackground = true, Name = "CaptionWorker" };
        _thread.SetApartmentState(ApartmentState.MTA);
    }

    public void Start() => _thread.Start();

    // ---------------------------------------------------------------- commands (any thread)

    public void SetCapture(bool on) => Post(() =>
    {
        if (on == _capturing) return;
        _capturing = on;
        _processor.SkipVisibleText(); // what is already on screen is history, not new speech
        Message?.Invoke(on ? "Capture started" : "Capture stopped");
        if (!on) _source.Invalidate("Capture stopped");
    });

    /// <summary>Start Auto Insert (also starts reading captions if needed). Stopping goes through <see cref="EmergencyStop"/>.</summary>
    public void StartAutoInsert() => Post(() =>
    {
        // Starting never checks for or opens Live Captions (that is the Open Live Captions button's job),
        // so it stays instant. If Live Captions is closed, typing begins once it is opened and connected.
        if (!_capturing)
        {
            _capturing = true;
            _processor.SkipVisibleText();
        }
        if (_gate.Enabled) return;
        _gate.SetEnabled(true);
        Message?.Invoke(_source.IsConnected
            ? "Auto Insert started: click into a textbox"
            : "Auto Insert started. Live Captions is not connected: click Open Live Captions.");
    });

    /// <summary>Opens Live Captions if it is closed (runs on the worker thread, which owns UI Automation).</summary>
    public void OpenLiveCaptions() => Post(() =>
    {
        Message?.Invoke(LiveCaptionsSource.OpenIfNotRunning());
        _wake.Set();
    });

    /// <summary>Session locked, system suspending, etc.</summary>
    public void SetSystemPause(bool paused, string why) => Post(() =>
    {
        _systemPause = paused;
        ApplyPause();
        Message?.Invoke(why);
    });

    /// <summary>Emergency stop. Takes effect immediately even in the middle of typing.</summary>
    public void EmergencyStop()
    {
        _inserter.Abort();
        Post(() =>
        {
            _gate.EmergencyStop();
            Message?.Invoke("Auto Insert stopped");
        });
    }

    public void ApplySettings(AppSettings s) => Post(() =>
    {
        bool engineChanged = s.StabilizationDelayMs != _settings.StabilizationDelayMs
                             || s.CommitMode != _settings.CommitMode
                             || s.SilenceFlushMs != _settings.SilenceFlushMs;
        _settings = s;
        _gate.AutoPauseOnFocusLoss = s.AutoPauseOnFocusLoss;
        if (engineChanged)
        {
            bool continueOutput = _processor.HasEmitted;
            _processor = new CaptionProcessor(ToProcessorOptions(s));
            _processor.Process(_lastSnapshot, _clock.Elapsed); // baseline on what is visible now
            if (continueOutput) _processor.ContinueOutput();
        }
    });

    private void Post(Action a)
    {
        _commands.Enqueue(a);
        _wake.Set();
    }

    private void ApplyPause() => _gate.SetPaused(_systemPause);

    private static CaptionProcessorOptions ToProcessorOptions(AppSettings s) => new()
    {
        StabilizationDelay = TimeSpan.FromMilliseconds(s.StabilizationDelayMs),
        Mode = s.CommitMode,
        SilenceFlush = TimeSpan.FromMilliseconds(s.SilenceFlushMs),
    };

    // ---------------------------------------------------------------- loop (worker thread)

    private void Run()
    {
        var ct = _cts.Token;
        int consecutiveErrors = 0;
        while (!ct.IsCancellationRequested)
        {
            int sleepMs;
            try
            {
                while (_commands.TryDequeue(out var cmd)) cmd();
                sleepMs = Tick();
                consecutiveErrors = 0;
            }
            catch (Exception ex)
            {
                // Never let one bad tick kill the loop; back off if errors repeat.
                consecutiveErrors++;
                _source.Invalidate($"Error: {ex.Message}");
                Message?.Invoke($"Error: {ex.GetType().Name}: {ex.Message}");
                sleepMs = Math.Min(5000, 250 * consecutiveErrors);
            }
            Publish();
            _wake.WaitOne(sleepMs);
        }
    }

    /// <summary>One polling cycle. Returns how long to sleep.</summary>
    private int Tick()
    {
        var now = _clock.Elapsed;
        if (!_capturing) return 500;

        // 1. Caption source
        if (!_source.IsConnected && now >= _nextConnectAttempt)
        {
            if (_source.TryConnect())
                Message?.Invoke($"Connected to Live Captions (pid {_source.ProcessId}, {_source.LastDiscoveryMs:F0} ms)");
            _nextConnectAttempt = now + TimeSpan.FromSeconds(1);
        }

        string? snapshot = null;
        if (_source.IsConnected)
        {
            long t0 = Stopwatch.GetTimestamp();
            snapshot = _source.Read();
            _readMsTotal += Stopwatch.GetElapsedTime(t0).TotalMilliseconds;
            _reads++;
            if (snapshot == null) Message?.Invoke(_source.Status);
        }

        // 2. Focus. Checked every tick while inserting, less often otherwise (status display only).
        if (_gate.Enabled || now >= _nextFocusCheck)
        {
            _lastFocus = _focus.GetFocused();
            _nextFocusCheck = now + TimeSpan.FromMilliseconds(500);
        }
        _gate.ObserveFocus(_lastFocus?.Info, now);
        if (_gate.Enabled && _gate.StatusReason != _lastGateReason)
        {
            _lastGateReason = _gate.StatusReason;
            Message?.Invoke($"Target: {_gate.StatusReason}");
        }

        // 3. Incremental caption processing
        if (snapshot != null)
        {
            if (!string.Equals(snapshot, _lastSnapshot, StringComparison.Ordinal))
            {
                _lastSnapshot = snapshot;
                _lastSnapshotChange = now;
                _snapshotChanges++;
            }
            string committed = _processor.Process(snapshot, now);
            bool skip = _gate.TakeSkipRequest();
            if (committed.Length > 0)
            {
                AppendTranscript(committed);
                _committedChars += committed.Length;
            }
            if (skip)
            {
                _processor.SkipVisibleText();
                _processor.ResetOutputSpacing();
            }
            else if (committed.Length > 0)
            {
                int dropped = _gate.DroppedChars;
                _gate.Offer(committed, now);
                if (_gate.DroppedChars > dropped && _gate.StatusReason != _lastDropReason)
                {
                    _lastDropReason = _gate.StatusReason;
                    Message?.Invoke($"Captions not inserted: {_gate.StatusReason}");
                }
                else if (_gate.DroppedChars == dropped)
                {
                    _lastDropReason = "";
                }
            }
        }
        else if (_gate.TakeSkipRequest())
        {
            _processor.SkipVisibleText();
            _processor.ResetOutputSpacing();
        }

        // 4. Insertion: re-check focus right before inserting.
        if (_gate.Status == GateStatus.Ready && _gate.QueuedText.Length > 0)
        {
            var current = _focus.GetFocused();
            _gate.ObserveFocus(current?.Info, now);
            var text = _gate.TakeInsertable(current?.Info);
            if (text != null && current != null) DoInsert(text, current);
        }

        // 5. Adaptive polling: slow down when captions have been idle for a while.
        int interval = _settings.PollIntervalMs;
        if (!_source.IsConnected) return 1000;
        // While Auto Insert is off, read Live Captions only once a second (the transcript still fills in).
        // This keeps the cross-process UI Automation calls into Live Captions to a minimum.
        if (!_gate.Enabled) return Math.Max(interval, 1000);
        if (now - _lastSnapshotChange > TimeSpan.FromSeconds(10) && _processor.PendingText.Length == 0)
            interval = Math.Min(1000, interval * 3);
        return interval;
    }

    private void DoInsert(string text, FocusSnapshot target)
    {
        var r = _inserter.Insert(text, target, _settings, _clipboard);
        _lastInsertMs = r.Milliseconds;
        switch (r.Outcome)
        {
            case InsertOutcome.Verified:
            case InsertOutcome.Unverified:
                _insertedChars += text.Length;
                _lastInsert = $"{r.Message}: \"{Shorten(text.Trim(), 60)}\" into {target.Info.Describe()}";
                break;
            default:
                _failedInserts++;
                _lastInsert = $"NOT inserted: {r.Message}";
                Message?.Invoke($"Insertion failed into {target.Info.Describe()}: {r.Message}. Text kept in preview: \"{Shorten(text.Trim(), 80)}\"");
                break;
        }
    }

    private void AppendTranscript(string text)
    {
        _transcript.Append(text);
        if (_transcript.Length > TranscriptMaxChars)
            _transcript.Remove(0, _transcript.Length - TranscriptMaxChars);
    }

    private void Publish()
    {
        var focusDesc = _lastFocus?.Info is { } f && !f.IsOwnProcess ? f.Describe() : "(this app or nothing focused)";
        var s = new WorkerStatus
        {
            Capturing = _capturing,
            Connected = _source.IsConnected,
            CaptionStatus = _capturing ? _source.Status : "Capture stopped",
            Pending = _processor.PendingText,
            Transcript = _transcript.ToString(),
            AutoInsert = _gate.Enabled,
            Paused = _gate.Paused,
            Gate = _gate.Status,
            GateReason = _gate.StatusReason,
            Target = focusDesc,
            LockedTarget = _gate.LockedTargetDescription,
            LockArmed = _gate.LockArmed,
            LastInsert = _lastInsert,
            AvgReadMs = _reads > 0 ? _readMsTotal / _reads : 0,
            LastInsertMs = _lastInsertMs,
            LastDiscoveryMs = _source.LastDiscoveryMs,
            Resets = _processor.Resets,
            CorrectionsIgnored = _processor.CorrectionsIgnored,
            InsertedChars = _insertedChars,
            FailedInserts = _failedInserts,
            TargetDetails = DescribeTarget(_lastFocus?.Info),
            CaptionSample = _lastSnapshot.Length <= 160 ? _lastSnapshot : "…" + _lastSnapshot[^160..],
            SnapshotChanges = _snapshotChanges,
            CommittedChars = _committedChars,
            DroppedChars = _gate.DroppedChars,
        };
        // Skip identical snapshots, but refresh at least every 2 s so the timing numbers stay current.
        var now = _clock.Elapsed;
        if (_lastPublished != null && _lastPublished with { AvgReadMs = s.AvgReadMs } == s
            && now - _lastPublishTime < TimeSpan.FromSeconds(2)) return;
        _lastPublished = s;
        _lastPublishTime = now;
        StatusChanged?.Invoke(s);
    }

    private static string DescribeTarget(TargetInfo? t) => t == null
        ? "none (UI Automation returned no focused element)"
        : $"process={t.ProcessName} pid={t.ProcessId} control={t.ControlType} class='{t.ClassName}' enabled={t.IsEnabled} " +
          $"readOnly={(t.IsReadOnly?.ToString() ?? "n/a")} password={t.IsPassword} textPattern={t.HasTextPattern} " +
          $"elevatedOrProtected={t.IsElevatedOrProtected} self={t.IsOwnProcess}";

    private static string Shorten(string s, int max) => s.Length <= max ? s : s[..max] + "…";

    public void Dispose()
    {
        _inserter.Abort();
        _cts.Cancel();
        _wake.Set();
        if (_thread.IsAlive) _thread.Join(TimeSpan.FromSeconds(3));
        _wake.Dispose();
        _cts.Dispose();
    }
}
