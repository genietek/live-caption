namespace CaptionBridge.Core;

/// <summary>What the Windows layer found about the currently focused element.</summary>
public sealed record TargetInfo
{
    /// <summary>Stable identity of the element: process id + window + UIA runtime id.</summary>
    public required string Identity { get; init; }
    public int ProcessId { get; init; }
    public string ProcessName { get; init; } = "";
    public string ControlType { get; init; } = "";
    public string ClassName { get; init; } = "";
    public string Name { get; init; } = "";
    public bool IsEnabled { get; init; } = true;
    public bool IsPassword { get; init; }
    /// <summary>From ValuePattern.IsReadOnly when the element has a ValuePattern, otherwise null.</summary>
    public bool? IsReadOnly { get; init; }
    public bool HasTextPattern { get; init; }
    public bool IsOwnProcess { get; init; }
    /// <summary>Target runs elevated (or we could not tell) while we are not; Windows blocks input to it.</summary>
    public bool IsElevatedOrProtected { get; init; }

    public string Describe() => $"{ProcessName} ({ControlType}{(string.IsNullOrEmpty(ClassName) ? "" : " " + ClassName)})";
}

public static class TargetClassifier
{
    /// <summary>Editors that expose a Document without a ValuePattern but accept typed text.</summary>
    private static readonly string[] KnownEditorClasses =
    {
        "RichEditD2DPT",   // Windows 11 Notepad
        "RICHEDIT50W", "RichEdit20W", "RichEdit20A",
        "_WwG",            // Microsoft Word
    };

    private static readonly string[] BlockedProcesses =
    {
        "LiveCaptions", "LogonUI", "consent", "CredentialUIBroker", "LockApp", "SecHealthUI",
    };

    /// <summary>Decides whether text may be inserted into the focused element. Returns a reason when not.</summary>
    public static bool IsInsertable(TargetInfo? t, out string reason)
    {
        if (t == null) { reason = "No focused element"; return false; }
        if (t.IsOwnProcess) { reason = "This app is focused"; return false; }
        if (BlockedProcesses.Any(p => string.Equals(p, t.ProcessName, StringComparison.OrdinalIgnoreCase)))
        { reason = $"{t.ProcessName} is a protected or excluded app"; return false; }
        if (t.IsElevatedOrProtected) { reason = "Target runs as administrator or is protected"; return false; }
        if (t.IsPassword) { reason = "Password field"; return false; }
        if (!t.IsEnabled) { reason = "Field is disabled"; return false; }
        if (t.IsReadOnly == true) { reason = "Field is read-only"; return false; }

        switch (t.ControlType)
        {
            case "Edit":
                reason = "";
                return true;
            case "Document":
                if (t.IsReadOnly == false) { reason = ""; return true; }
                if (t.IsReadOnly == null && t.HasTextPattern
                    && KnownEditorClasses.Contains(t.ClassName, StringComparer.OrdinalIgnoreCase))
                { reason = ""; return true; }
                reason = "Document is not verified as editable";
                return false;
            default:
                reason = $"Not a text field ({t.ControlType})";
                return false;
        }
    }
}

public enum GateStatus
{
    Disabled,
    Paused,
    NoValidTarget,
    WaitingForLockedTarget,
    Ready,
}

/// <summary>
/// Decides when committed captions may be inserted and into which target. Pure logic, no Windows calls,
/// so the focus policy is unit tested with mock targets.
///
/// Policy:
///  - Nothing is inserted unless Auto Insert is enabled and not paused.
///  - Enabling, resuming, or a change of target asks the caller to skip currently visible captions
///    (<see cref="TakeSkipRequest"/>), so stale text never lands in a newly focused field.
///  - With auto-pause on (default), captions committed while no valid field is focused are dropped.
///    With it off, they are held (bounded) and inserted only if the same field gets focus back.
///  - Lock mode only ever inserts into the locked field.
///  - Emergency stop disables insertion and drops everything queued.
/// </summary>
public sealed class InsertionGate
{
    public const int MaxQueuedChars = 2000;
    public static readonly TimeSpan MaxQueueAge = TimeSpan.FromSeconds(10);

    private string _queue = "";
    private TimeSpan _queuedAt;
    private string? _queueTargetId;
    private string? _lastValidTargetId;
    private bool _skipRequested = true;
    private bool _lockArmed;

    public bool Enabled { get; private set; }
    public bool Paused { get; private set; }
    public bool AutoPauseOnFocusLoss { get; set; } = true;
    public string? LockedTargetId { get; private set; }
    public string? LockedTargetDescription { get; private set; }
    public bool LockArmed => _lockArmed;
    public int DroppedChars { get; private set; }
    public GateStatus Status { get; private set; } = GateStatus.Disabled;
    public string StatusReason { get; private set; } = "Auto Insert is off";
    public string QueuedText => _queue;

    public void SetEnabled(bool on)
    {
        if (on && !Enabled) _skipRequested = true;
        Enabled = on;
        if (!on) ClearQueue();
    }

    public void SetPaused(bool paused)
    {
        if (!paused && Paused) _skipRequested = true;
        Paused = paused;
        if (paused) ClearQueue();
    }

    /// <summary>Lock to the next valid field that gets focus (the app itself is focused when the button is clicked).</summary>
    public void ArmLock() { _lockArmed = true; LockedTargetId = null; LockedTargetDescription = null; }

    public void Unlock() { _lockArmed = false; LockedTargetId = null; LockedTargetDescription = null; }

    public void EmergencyStop()
    {
        Enabled = false;
        ClearQueue();
        Status = GateStatus.Disabled;
        StatusReason = "Stopped";
    }

    /// <summary>True once after anything that means visible captions must not be inserted. Clears the flag.</summary>
    public bool TakeSkipRequest()
    {
        bool r = _skipRequested;
        _skipRequested = false;
        return r;
    }

    /// <summary>Call every tick with the currently focused element, before committing new captions.</summary>
    public void ObserveFocus(TargetInfo? focused, TimeSpan now)
    {
        bool valid = TargetClassifier.IsInsertable(focused, out var reason);

        if (valid && _lockArmed)
        {
            _lockArmed = false;
            LockedTargetId = focused!.Identity;
            LockedTargetDescription = focused.Describe();
        }

        if (valid && LockedTargetId != null && focused!.Identity != LockedTargetId)
        {
            valid = false;
            reason = $"Locked to {LockedTargetDescription}";
        }

        if (valid && focused!.Identity != _lastValidTargetId)
        {
            if (_lastValidTargetId != null) _skipRequested = true; // new field: no stale captions
            _lastValidTargetId = focused.Identity;
            if (_queueTargetId != focused.Identity) ClearQueue();
        }

        if (_queue.Length > 0 && now - _queuedAt > MaxQueueAge) ClearQueue();

        if (!Enabled) { Status = GateStatus.Disabled; StatusReason = "Auto Insert is off"; }
        else if (Paused) { Status = GateStatus.Paused; StatusReason = "Paused"; }
        else if (valid) { Status = GateStatus.Ready; StatusReason = $"Ready: {focused!.Describe()}"; }
        else if (LockedTargetId != null) { Status = GateStatus.WaitingForLockedTarget; StatusReason = reason; }
        else
        {
            Status = GateStatus.NoValidTarget;
            StatusReason = reason;
            if (AutoPauseOnFocusLoss) ClearQueue();
        }
    }

    /// <summary>Offer newly committed caption text. It is queued for insertion or dropped by policy.</summary>
    public void Offer(string text, TimeSpan now)
    {
        if (text.Length == 0) return;
        bool hold = Status == GateStatus.Ready
                    || (!AutoPauseOnFocusLoss && Enabled && !Paused && _lastValidTargetId != null);
        if (!hold) { DroppedChars += text.Length; return; }

        if (_queue.Length == 0)
        {
            _queuedAt = now;
            _queueTargetId = _lastValidTargetId;
        }
        _queue += text;
        if (_queue.Length > MaxQueuedChars)
        {
            DroppedChars += _queue.Length - MaxQueuedChars;
            _queue = _queue[^MaxQueuedChars..];
        }
    }

    /// <summary>Returns the text to insert now into <paramref name="focused"/>, or null. Removes it from the queue.</summary>
    public string? TakeInsertable(TargetInfo? focused)
    {
        if (_queue.Length == 0 || Status != GateStatus.Ready || focused == null) return null;
        if (focused.Identity != _queueTargetId) { ClearQueue(); return null; }
        var text = _queue;
        _queue = "";
        return text;
    }

    // Failed insertions are reported, never retried, so a field never gets the same caption twice.
    private void ClearQueue()
    {
        DroppedChars += _queue.Length;
        _queue = "";
        _queueTargetId = null;
    }
}

public static class ClipboardPolicy
{
    /// <summary>
    /// Restore the user's clipboard only if nobody changed it after we put the caption there.
    /// Windows bumps the clipboard sequence number on every change.
    /// </summary>
    public static bool ShouldRestore(uint sequenceAfterOurSet, uint sequenceNow) => sequenceAfterOurSet == sequenceNow;
}

public static class TextSanitizer
{
    /// <summary>
    /// Makes caption text safe to type: no newlines or tabs (Enter could send a message), no control characters.
    /// Letters, accents, ñ, ¿ ¡ and all other Unicode text are kept as they are.
    /// </summary>
    public static string ForInsertion(string text)
    {
        var sb = new System.Text.StringBuilder(text.Length);
        foreach (var ch in text)
        {
            if (ch is '\r' or '\n' or '\t') sb.Append(' ');
            else if (char.IsControl(ch)) continue;
            else sb.Append(ch);
        }
        return sb.ToString();
    }
}
