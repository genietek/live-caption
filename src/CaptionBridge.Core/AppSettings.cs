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

/// <summary>What the window's X button does.</summary>
public enum CloseAction
{
    /// <summary>Ask each time (keep in tray, or close the app).</summary>
    Ask,

    /// <summary>Hide the window and keep running in the tray. Exit from the tray menu closes the app.</summary>
    KeepInTray,

    /// <summary>Close the app.</summary>
    Exit,
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

    /// <summary>What the window's X button does.</summary>
    public CloseAction CloseAction { get; set; } = CloseAction.Ask;
    public bool StartCaptureOnLaunch { get; set; } = true;

    /// <summary>Whether the Transcript section of the main window is expanded.</summary>
    public bool ShowTranscript { get; set; }
    /// <summary>Global hotkey that starts Auto Insert. Empty disables it.</summary>
    public string HotkeyStart { get; set; } = "F8";

    /// <summary>Global hotkey that stops Auto Insert immediately. Empty disables it.</summary>
    public string HotkeyStop { get; set; } = "F9";

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
        try
        {
            var s = JsonSerializer.Deserialize<AppSettings>(json, Json) ?? new AppSettings();
            // Files from 0.1.x had different hotkeys (Ctrl+Alt+Shift+...); move them to the F8/F9 defaults.
            if (!json.Contains("\"HotkeyStart\"", StringComparison.Ordinal))
            {
                s.HotkeyStart = "F8";
                s.HotkeyStop = "F9";
            }
            return s.Normalize();
        }
        catch (JsonException) { return new AppSettings(); }
    }
}

/// <summary>Parses hotkey strings like "Ctrl+Alt+Shift+I".</summary>
public static class HotkeyParser
{
    [Flags]
    public enum Modifiers : uint { None = 0, Alt = 1, Ctrl = 2, Shift = 4, Win = 8 }

    /// <summary>
    /// Accepts any single key, alone or with Ctrl/Alt/Shift ("F8", "Pause", "Ctrl+Home", "Ctrl+Alt+I", "NumPad5").
    /// Key names are letters/digits only (Windows key names such as "PageUp" or "OemTilde"); the app maps them
    /// to virtual keys. Rejects empty strings, a modifier on its own, two keys, Win combos and Alt+F4.
    /// Keys that would get in the way of normal typing are allowed; see <see cref="IsRisky"/>.
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
                    key = raw;
                    break;
            }
        }
        if (key.Length == 0 || !key.All(char.IsAsciiLetterOrDigit)) return false;
        bool functionKey = key.Length >= 2 && (key[0] is 'F' or 'f') && key.AsSpan(1).IndexOfAnyExceptInRange('0', '9') < 0;
        if (key.Length == 1 || functionKey) key = key.ToUpperInvariant();
        return !(key.Equals("F4", StringComparison.OrdinalIgnoreCase) && mods == Modifiers.Alt);
    }

    /// <summary>
    /// True for hotkeys that would take a key away from normal typing or editing in every app: a letter, digit,
    /// Space, Enter, Tab, Backspace, Delete, arrow or punctuation key without Ctrl/Alt, or Ctrl+A/C/V/X/Y/Z/S.
    /// The picker asks for confirmation before saving these.
    /// </summary>
    public static bool IsRisky(string? text)
    {
        if (!TryParse(text, out var mods, out var key)) return false;
        bool ctrlOrAlt = (mods & (Modifiers.Ctrl | Modifiers.Alt)) != 0;
        string k = key.ToUpperInvariant();
        if (!ctrlOrAlt)
        {
            if (k.Length == 1) return true; // letter or digit
            if (k.StartsWith("OEM", StringComparison.Ordinal)) return true; // punctuation
            if (k is "SPACE" or "ENTER" or "RETURN" or "TAB" or "BACK" or "BACKSPACE" or "DELETE"
                  or "LEFT" or "RIGHT" or "UP" or "DOWN" or "HOME" or "END") return true;
        }
        return mods == Modifiers.Ctrl && k is "A" or "C" or "V" or "X" or "Y" or "Z" or "S";
    }

    /// <summary>Builds the canonical text for a hotkey, for example "Ctrl+Alt+I" or "F8".</summary>
    public static string Format(Modifiers mods, string key)
    {
        var parts = new List<string>();
        if (mods.HasFlag(Modifiers.Ctrl)) parts.Add("Ctrl");
        if (mods.HasFlag(Modifiers.Alt)) parts.Add("Alt");
        if (mods.HasFlag(Modifiers.Shift)) parts.Add("Shift");
        parts.Add(key.Length == 1 ? key.ToUpperInvariant() : key);
        return string.Join("+", parts);
    }

    /// <summary>Canonical form of a valid hotkey ("ctrl + alt+i" becomes "Ctrl+Alt+I"), or null when it is not valid.</summary>
    public static string? Normalize(string? text) =>
        TryParse(text, out var mods, out var key) ? Format(mods, key) : null;

    /// <summary>True when both strings are valid and name the same key combination.</summary>
    public static bool SameKey(string? a, string? b)
    {
        var na = Normalize(a);
        return na != null && string.Equals(na, Normalize(b), StringComparison.OrdinalIgnoreCase);
    }
}
