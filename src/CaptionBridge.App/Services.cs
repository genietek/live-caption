using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows;
using System.Windows.Threading;
using CaptionBridge.Core;

namespace CaptionBridge;

/// <summary>Settings in %APPDATA%\CaptionBridge\settings.json (per user, no admin rights needed).</summary>
internal static class SettingsStore
{
    public static string Folder { get; } =
        Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "CaptionBridge");

    public static string FilePath { get; } = Path.Combine(Folder, "settings.json");

    public static AppSettings Load()
    {
        try
        {
            return File.Exists(FilePath) ? AppSettings.FromJson(File.ReadAllText(FilePath)) : new AppSettings();
        }
        catch (IOException) { return new AppSettings(); }
        catch (UnauthorizedAccessException) { return new AppSettings(); }
    }

    public static string? Save(AppSettings s)
    {
        try
        {
            Directory.CreateDirectory(Folder);
            string tmp = FilePath + ".tmp";
            File.WriteAllText(tmp, s.Normalize().ToJson());
            File.Move(tmp, FilePath, overwrite: true);
            return null;
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            return ex.Message;
        }
    }
}

/// <summary>Toggle is used when Start and Stop are set to the same key: one key turns Auto Insert on and off.</summary>
internal enum HotkeyAction { Start = 1, Stop = 2, Toggle = 3 }

/// <summary>Global hotkeys via RegisterHotKey. Only the configured combos are seen; no keyboard hook, no key logging.</summary>
internal sealed class HotkeyManager : IDisposable
{
    private readonly IntPtr _hwnd;
    private readonly List<int> _registered = new();

    public HotkeyManager(IntPtr hwnd) => _hwnd = hwnd;

    /// <summary>Registers the hotkeys from settings. Returns a message per hotkey that could not be registered.</summary>
    public List<string> Apply(AppSettings s)
    {
        UnregisterAll();
        var problems = new List<string>();
        if (HotkeyParser.SameKey(s.HotkeyStart, s.HotkeyStop))
        {
            Register(HotkeyAction.Toggle, s.HotkeyStart, problems);
            return problems;
        }
        if (!string.IsNullOrWhiteSpace(s.HotkeyStart)) Register(HotkeyAction.Start, s.HotkeyStart, problems);
        if (!string.IsNullOrWhiteSpace(s.HotkeyStop)) Register(HotkeyAction.Stop, s.HotkeyStop, problems);
        return problems;
    }

    private void Register(HotkeyAction action, string text, List<string> problems)
    {
        string name = action == HotkeyAction.Toggle ? "Start/Stop" : action.ToString();
        if (!HotkeyParser.TryParse(text, out var mods, out var key))
        {
            problems.Add($"Hotkey '{text}' for {name} is not allowed. Use an F-key (like F8), or two of Ctrl/Alt/Shift plus a letter or digit.");
            return;
        }
        uint vk = VirtualKey(key);
        if (vk == 0)
        {
            problems.Add($"Hotkey '{text}' for {name} uses a key Windows does not know. Pick another key in Settings.");
            return;
        }
        if (NativeMethods.RegisterHotKey(_hwnd, (int)action, (uint)mods | NativeMethods.MOD_NOREPEAT, vk))
            _registered.Add((int)action);
        else
            problems.Add($"Hotkey '{text}' for {name} is already used by another app (error {Marshal.GetLastWin32Error()}). Pick another key in Settings.");
    }

    /// <summary>Virtual-key code for a key name: a letter or digit, F1–F24, or any WPF key name ("PageUp", "NumPad5").</summary>
    internal static uint VirtualKey(string key)
    {
        if (key.Length == 1 && char.IsAsciiLetterOrDigit(key[0])) return char.ToUpperInvariant(key[0]);
        if (key.Length >= 2 && key[0] is 'F' or 'f' && int.TryParse(key.AsSpan(1), out int f) && f is >= 1 and <= 24)
            return (uint)(0x70 + f - 1);
        return Enum.TryParse<System.Windows.Input.Key>(key, ignoreCase: true, out var k)
            ? (uint)System.Windows.Input.KeyInterop.VirtualKeyFromKey(k)
            : 0;
    }

    public void UnregisterAll()
    {
        foreach (var id in _registered) NativeMethods.UnregisterHotKey(_hwnd, id);
        _registered.Clear();
    }

    public void Dispose() => UnregisterAll();
}

/// <summary>WPF clipboard calls, marshalled to the UI thread (clipboard APIs need an STA thread).</summary>
internal sealed class WpfClipboardAccess : IClipboardAccess
{
    private readonly Dispatcher _ui;

    public WpfClipboardAccess(Dispatcher ui) => _ui = ui;

    public object? Save() => _ui.Invoke(() => Retry(() =>
    {
        var current = Clipboard.GetDataObject();
        if (current == null) return null;
        var formats = current.GetFormats(false);
        if (formats.Length == 0) return null;

        // Copy each format we can read; formats that can't be copied (rare) are lost, as documented.
        var copy = new DataObject();
        foreach (var f in formats)
        {
            try
            {
                var data = current.GetData(f, false);
                if (data != null) copy.SetData(f, data);
            }
            catch (Exception ex) when (ex is COMException or OutOfMemoryException or ExternalException) { }
        }
        return (object)copy;
    }));

    public uint SetText(string text) => _ui.Invoke(() => Retry(() =>
    {
        Clipboard.SetDataObject(new DataObject(DataFormats.UnicodeText, text), copy: true);
        return NativeMethods.GetClipboardSequenceNumber();
    }));

    public void Restore(object? saved) => _ui.Invoke(() => Retry(() =>
    {
        if (saved is IDataObject d) Clipboard.SetDataObject(d, copy: true);
        else Clipboard.Clear();
        return 0;
    }));

    /// <summary>The clipboard may be briefly locked by another app; retry a few times.</summary>
    private static T Retry<T>(Func<T> action)
    {
        for (int attempt = 0; ; attempt++)
        {
            try { return action(); }
            catch (COMException) when (attempt < 5) { Thread.Sleep(30); }
        }
    }
}
