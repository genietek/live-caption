using System.Text.Json;
using System.Text.Json.Serialization;

namespace CaptionBridge.Core;

public enum InsertionMethod
{
    /// <summary>Types the text as Unicode key events (SendInput). Does not touch the clipboard.</summary>
    DirectTyping,

    /// <summary>Puts the text on the clipboard, presses Ctrl+V, then restores the previous clipboard when safe.</summary>
    Clipboard,
}

public sealed class AppSettings
{
    public bool AutoInsertOnStartup { get; set; }
    public int StabilizationDelayMs { get; set; } = 500;
    public int PollIntervalMs { get; set; } = 200;
    public CommitMode CommitMode { get; set; } = CommitMode.StablePhrases;
    public int SilenceFlushMs { get; set; } = 1500;
    public InsertionMethod InsertionMethod { get; set; } = InsertionMethod.DirectTyping;
    public int ClipboardRestoreDelayMs { get; set; } = 250;
    public bool AutoPauseOnFocusLoss { get; set; } = true;
    public bool VerifyInsertion { get; set; } = true;
    public bool MinimizeToTray { get; set; } = true;
    public bool StartMinimized { get; set; }
    public bool StartCaptureOnLaunch { get; set; } = true;
    public bool HotkeysEnabled { get; set; } = true;
    public string HotkeyToggleInsert { get; set; } = "Ctrl+Alt+Shift+I";
    public string HotkeyPauseResume { get; set; } = "Ctrl+Alt+Shift+P";
    public string HotkeyStop { get; set; } = "Ctrl+Alt+Shift+S";

    /// <summary>Clamp values to safe ranges (protects against hand-edited settings files).</summary>
    public AppSettings Normalize()
    {
        StabilizationDelayMs = Math.Clamp(StabilizationDelayMs, 0, 10_000);
        PollIntervalMs = Math.Clamp(PollIntervalMs, 50, 2_000);
        SilenceFlushMs = Math.Clamp(SilenceFlushMs, 200, 30_000);
        ClipboardRestoreDelayMs = Math.Clamp(ClipboardRestoreDelayMs, 50, 5_000);
        return this;
    }

    private static readonly JsonSerializerOptions Json = new()
    {
        WriteIndented = true,
        Converters = { new JsonStringEnumConverter() },
    };

    public string ToJson() => JsonSerializer.Serialize(this, Json);

    public static AppSettings FromJson(string json)
    {
        try { return (JsonSerializer.Deserialize<AppSettings>(json, Json) ?? new AppSettings()).Normalize(); }
        catch (JsonException) { return new AppSettings(); }
    }
}

/// <summary>Parses hotkey strings like "Ctrl+Alt+Shift+I".</summary>
public static class HotkeyParser
{
    [Flags]
    public enum Modifiers : uint { None = 0, Alt = 1, Ctrl = 2, Shift = 4, Win = 8 }

    /// <summary>
    /// Returns false for empty strings, unknown keys, Win combos, and anything with fewer than two of
    /// Ctrl/Alt/Shift. Requiring two modifiers keeps clear of standard shortcuts like Ctrl+C or Alt+F4.
    /// </summary>
    public static bool TryParse(string? text, out Modifiers mods, out string key)
    {
        mods = Modifiers.None;
        key = "";
        if (string.IsNullOrWhiteSpace(text)) return false;
        foreach (var raw in text.Split('+', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries))
        {
            switch (raw.ToUpperInvariant())
            {
                case "CTRL": case "CONTROL": mods |= Modifiers.Ctrl; break;
                case "ALT": mods |= Modifiers.Alt; break;
                case "SHIFT": mods |= Modifiers.Shift; break;
                case "WIN": return false; // Win+ combos belong to Windows
                default:
                    if (key.Length > 0) return false;
                    key = raw.ToUpperInvariant();
                    break;
            }
        }
        bool keyOk = (key.Length == 1 && char.IsAsciiLetterOrDigit(key[0]))
                     || (key.Length is 2 or 3 && key[0] == 'F' && int.TryParse(key.AsSpan(1), out var f) && f is >= 1 and <= 24);
        int modCount = System.Numerics.BitOperations.PopCount((uint)mods);
        return keyOk && modCount >= 2 && (mods & (Modifiers.Ctrl | Modifiers.Alt)) != 0;
    }
}
