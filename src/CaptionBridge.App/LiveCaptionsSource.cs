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
                Status = "Live Captions is not running. Press Win+Ctrl+L or use the Launch button.";
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
