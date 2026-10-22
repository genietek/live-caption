using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Windows.Automation;
using CaptionBridge.Core;

namespace CaptionBridge;

/// <param name="ForegroundWindow">Top-level window that had focus when probed; insertion aborts if it changes.</param>
internal sealed record FocusSnapshot(TargetInfo Info, AutomationElement? Element, IntPtr ForegroundWindow);

/// <summary>
/// Reads the focused UI Automation element and the facts needed to decide whether it is a safe, editable
/// target. Properties are fetched in one batched cache request to keep cross-process calls low.
/// It never reads the field's text except for optional insertion verification.
/// </summary>
internal sealed class FocusProbe
{
    private static readonly int OwnPid = Environment.ProcessId;
    private static readonly bool SelfElevated = NativeMethods.IsProcessElevated(OwnPid) == true;

    private readonly CacheRequest _cache;
    private readonly Dictionary<int, (string name, bool blocked)> _processInfo = new();

    public FocusProbe()
    {
        _cache = new CacheRequest { AutomationElementMode = AutomationElementMode.Full, TreeScope = TreeScope.Element };
        _cache.Add(AutomationElement.ControlTypeProperty);
        _cache.Add(AutomationElement.ClassNameProperty);
        _cache.Add(AutomationElement.ProcessIdProperty);
        _cache.Add(AutomationElement.IsEnabledProperty);
        _cache.Add(AutomationElement.IsPasswordProperty);
        _cache.Add(AutomationElement.IsValuePatternAvailableProperty);
        _cache.Add(AutomationElement.IsTextPatternAvailableProperty);
        _cache.Add(ValuePattern.IsReadOnlyProperty);
    }

    public FocusSnapshot? GetFocused()
    {
        IntPtr fg = NativeMethods.GetForegroundWindow();
        NativeMethods.GetWindowThreadProcessId(fg, out uint fgPid);
        if (fgPid == OwnPid)
            return new FocusSnapshot(new TargetInfo { Identity = "self", ProcessId = OwnPid, ProcessName = "CaptionBridge", IsOwnProcess = true }, null, fg);

        AutomationElement? el;
        try
        {
            el = AutomationElement.FocusedElement?.GetUpdatedCache(_cache);
        }
        catch (Exception ex) when (LiveCaptionsSource.IsUiaFailure(ex))
        {
            return null;
        }
        if (el == null) return null;

        try
        {
            int pid = (int)el.GetCachedPropertyValue(AutomationElement.ProcessIdProperty);
            var (procName, blocked) = GetProcessInfo(pid);
            var controlType = (ControlType)el.GetCachedPropertyValue(AutomationElement.ControlTypeProperty);
            bool hasValue = (bool)el.GetCachedPropertyValue(AutomationElement.IsValuePatternAvailableProperty);
            bool? readOnly = null;
            if (hasValue && el.GetCachedPropertyValue(ValuePattern.IsReadOnlyProperty, true) is bool ro) readOnly = ro;

            var info = new TargetInfo
            {
                Identity = $"{pid}:{fg}:{string.Join(".", el.GetRuntimeId())}",
                ProcessId = pid,
                ProcessName = procName,
                ControlType = controlType.ProgrammaticName.Replace("ControlType.", ""),
                ClassName = el.GetCachedPropertyValue(AutomationElement.ClassNameProperty) as string ?? "",
                IsEnabled = (bool)el.GetCachedPropertyValue(AutomationElement.IsEnabledProperty),
                IsPassword = (bool)el.GetCachedPropertyValue(AutomationElement.IsPasswordProperty),
                IsReadOnly = readOnly,
                HasTextPattern = (bool)el.GetCachedPropertyValue(AutomationElement.IsTextPatternAvailableProperty),
                IsOwnProcess = pid == OwnPid,
                IsElevatedOrProtected = blocked,
            };
            return new FocusSnapshot(info, el, fg);
        }
        catch (Exception ex) when (LiveCaptionsSource.IsUiaFailure(ex) || ex is InvalidCastException)
        {
            return null;
        }
    }

    private (string name, bool blocked) GetProcessInfo(int pid)
    {
        if (_processInfo.TryGetValue(pid, out var cached)) return cached;
        if (_processInfo.Count > 128) _processInfo.Clear();

        string name;
        try { using var p = Process.GetProcessById(pid); name = p.ProcessName; }
        catch (Exception) { name = $"pid {pid}"; }

        // Windows blocks input from a normal app into an elevated one (UIPI). Don't try.
        bool? elevated = NativeMethods.IsProcessElevated(pid);
        bool blocked = !SelfElevated && elevated != false;

        var info = (name, blocked);
        _processInfo[pid] = info;
        return info;
    }
}
