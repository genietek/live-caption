using System;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Windows.Automation;

namespace CaptionBridge;

/// <summary>
/// Reads the Windows 11 Live Captions text through UI Automation.
/// Finds the window by its UIA class name (not the localized title) and the caption element by AutomationId,
/// caches both, and drops the cache as soon as a read fails so the next call reconnects.
/// Must be used from one MTA worker thread.
/// </summary>
internal sealed class LiveCaptionsSource
{
    public const string WindowClassName = "LiveCaptionsDesktopWindow";
    public const string CaptionAutomationId = "CaptionsTextBlock";
    public const string ProcessName = "LiveCaptions";

    private static readonly Condition WindowCondition =
        new PropertyCondition(AutomationElement.ClassNameProperty, WindowClassName);
    private static readonly Condition TextCondition =
        new PropertyCondition(AutomationElement.AutomationIdProperty, CaptionAutomationId);

    private AutomationElement? _window;
    private AutomationElement? _text;

    public bool IsConnected => _text != null;
    public int ProcessId { get; private set; }
    public string Status { get; private set; } = "Not connected";
    public double LastDiscoveryMs { get; private set; }

    /// <summary>Tries to find the window and caption element. Cheap enough to call about once a second.</summary>
    public bool TryConnect()
    {
        var sw = Stopwatch.StartNew();
        try
        {
            if (!IsProcessRunning())
            {
                Status = "Live Captions is not running. Start Auto Insert or click Open Live Captions to open it.";
                return false;
            }

            var window = AutomationElement.RootElement.FindFirst(TreeScope.Children, WindowCondition);
            if (window == null)
            {
                Status = $"Live Captions is running but its window ({WindowClassName}) was not found.";
                return false;
            }

            var text = window.FindFirst(TreeScope.Descendants, TextCondition);
            if (text == null)
            {
                Status = $"Live Captions window found, but the caption element ({CaptionAutomationId}) was not. " +
                         "It may still be starting, or this Windows version uses a different layout.";
                return false;
            }

            _window = window;
            _text = text;
            ProcessId = window.Current.ProcessId;
            LastDiscoveryMs = sw.Elapsed.TotalMilliseconds;
            Status = "Connected to Live Captions";
            return true;
        }
        catch (Exception ex) when (IsUiaFailure(ex))
        {
            Invalidate($"Could not access Live Captions: {ex.Message}");
            return false;
        }
    }

    /// <summary>Returns the current caption text, or null if not connected or the element went away.</summary>
    public string? Read()
    {
        if (_text == null) return null;
        try
        {
            return _text.Current.Name ?? string.Empty;
        }
        catch (Exception ex) when (IsUiaFailure(ex))
        {
            Invalidate("Live Captions closed or restarted. Reconnecting...");
            return null;
        }
    }

    public void Invalidate(string status)
    {
        _window = null;
        _text = null;
        Status = status;
    }

    public static bool IsProcessRunning()
    {
        var procs = Process.GetProcessesByName(ProcessName);
        foreach (var p in procs) p.Dispose();
        return procs.Length > 0;
    }

    private static DateTime _lastLaunch = DateTime.MinValue;

    /// <summary>True when the Live Captions window exists (the process alone is not enough: it can outlive its window).</summary>
    public static bool IsWindowOpen()
    {
        try
        {
            return AutomationElement.RootElement.FindFirst(TreeScope.Children, WindowCondition) != null;
        }
        catch (Exception ex) when (IsUiaFailure(ex))
        {
            return IsProcessRunning();
        }
    }

    /// <summary>
    /// Opens Live Captions if its window is not open. Starts %WINDIR%\System32\LiveCaptions.exe directly, and
    /// then falls back to Win+Ctrl+L only if that file is missing or fails. While a launch from the last 5 s is still
    /// starting (process up, window not yet), it does not launch again, so rapid presses never open two copies and
    /// never send the toggling shortcut twice. Call from the worker (MTA) thread. Returns a short message for the status line.
    /// </summary>
    public static string OpenIfNotRunning()
    {
        if (IsWindowOpen()) return "Live Captions is already open";
        bool running = IsProcessRunning();
        if (running && DateTime.UtcNow - _lastLaunch < TimeSpan.FromSeconds(5)) return "Live Captions is starting…";
        _lastLaunch = DateTime.UtcNow;
        string exe = System.IO.Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "LiveCaptions.exe");
        try
        {
            if (System.IO.File.Exists(exe))
            {
                using var p = Process.Start(new ProcessStartInfo(exe) { UseShellExecute = true });
                return "Opening Live Captions…";
            }
        }
        catch (Exception ex) when (ex is System.ComponentModel.Win32Exception or InvalidOperationException) { }

        if (running) return "Live Captions is running but its window is hidden. Press Win+Ctrl+L to show it.";
        LaunchWithShortcut();
        return "Opening Live Captions (Win+Ctrl+L)…";
    }

    /// <summary>Toggles Live Captions with its documented Windows shortcut, Win+Ctrl+L.</summary>
    public static void LaunchWithShortcut()
    {
        var inputs = new[]
        {
            NativeMethods.Key(NativeMethods.VK_LWIN, false),
            NativeMethods.Key(NativeMethods.VK_CONTROL, false),
            NativeMethods.Key(NativeMethods.VK_L, false),
            NativeMethods.Key(NativeMethods.VK_L, true),
            NativeMethods.Key(NativeMethods.VK_CONTROL, true),
            NativeMethods.Key(NativeMethods.VK_LWIN, true),
        };
        NativeMethods.Send(inputs);
    }

    internal static bool IsUiaFailure(Exception ex) =>
        ex is ElementNotAvailableException or COMException or InvalidOperationException
           or TimeoutException or ArgumentException or UnauthorizedAccessException;
}
