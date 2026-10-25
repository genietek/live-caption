using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using CaptionBridge.Core;

namespace CaptionBridge;

/// <summary>
/// Small "press a key" dialog for choosing a hotkey. It only listens while it is open and focused;
/// nothing is logged. Esc cancels, Backspace turns the hotkey off. Any other key or combination can be used;
/// keys needed for normal typing must be pressed twice to confirm.
/// </summary>
internal sealed class HotkeyCaptureDialog : Window
{
    private readonly TextBlock _keyText = new()
    {
        FontSize = 20, FontWeight = FontWeights.SemiBold, HorizontalAlignment = HorizontalAlignment.Center,
        Margin = new Thickness(0, 10, 0, 6),
    };
    private readonly TextBlock _hint = new() { TextWrapping = TextWrapping.Wrap, FontSize = 12, Foreground = Brushes.DimGray };

    private string? _pendingRisky; // a typing key pressed once; pressing it again confirms

    /// <summary>The chosen hotkey ("F8", "Ctrl+Alt+I"), or "" when the user turned it off. Null when cancelled.</summary>
    public string? Result { get; private set; }

    public HotkeyCaptureDialog(string forWhat, string current)
    {
        Title = $"{forWhat} hotkey";
        Width = 320;
        SizeToContent = SizeToContent.Height;
        ResizeMode = ResizeMode.NoResize;
        WindowStartupLocation = WindowStartupLocation.CenterOwner;
        ShowInTaskbar = false;
        Background = SystemColors.WindowBrush;
        FontFamily = new FontFamily("Segoe UI");

        var panel = new StackPanel { Margin = new Thickness(16) };
        panel.Children.Add(new TextBlock { Text = $"Press the new key for {forWhat}.", FontSize = 14, TextWrapping = TextWrapping.Wrap });
        _keyText.Text = string.IsNullOrEmpty(current) ? "(none)" : current;
        panel.Children.Add(_keyText);
        ShowHint();
        panel.Children.Add(_hint);
        var cancel = new Button { Content = "Cancel", Padding = new Thickness(14, 3, 14, 3), Margin = new Thickness(0, 10, 0, 0), HorizontalAlignment = HorizontalAlignment.Right, Focusable = false };
        cancel.Click += (_, _) => Close();
        panel.Children.Add(cancel);
        Content = panel;

        PreviewKeyDown += OnKeyDown;
        PreviewKeyUp += (_, _) => { if (Result == null && Keyboard.Modifiers == ModifierKeys.None) ShowHint(); };
    }

    private void ShowHint(string? error = null)
    {
        _hint.Text = error ?? "Press any key or combination, for example F8, Pause, Ctrl+Home or Ctrl+Alt+I. Esc cancels. Backspace turns this hotkey off.";
        _hint.Foreground = error == null ? Brushes.DimGray : Brushes.Firebrick;
    }

    private void OnKeyDown(object sender, KeyEventArgs e)
    {
        e.Handled = true; // keep Alt/F10 from opening the system menu, and Space/Enter from pressing buttons
        var key = e.Key switch { Key.System => e.SystemKey, Key.ImeProcessed => e.ImeProcessedKey, _ => e.Key };
        var mods = Keyboard.Modifiers;

        if (mods == ModifierKeys.None && key == Key.Escape) { Close(); return; }
        if (mods == ModifierKeys.None && key == Key.Back) { Finish(""); return; }

        string? name = KeyName(key);
        if (name == null)
        {
            // A modifier on its own: show what is held so far and keep waiting.
            if (key is Key.LeftCtrl or Key.RightCtrl or Key.LeftAlt or Key.RightAlt or Key.LeftShift or Key.RightShift or Key.LWin or Key.RWin)
            {
                _keyText.Text = ModText(mods) + "…";
                return;
            }
            ShowHint($"{key} can't be used as a hotkey. Pick another key.");
            return;
        }

        string text = ModText(mods) + name;
        _keyText.Text = text;
        if (mods.HasFlag(ModifierKeys.Windows))
        {
            ShowHint("Win key combinations belong to Windows. Pick another key.");
            return;
        }
        var normalized = HotkeyParser.Normalize(text);
        if (normalized == null)
        {
            ShowHint("Alt+F4 closes windows. Pick another key.");
            return;
        }
        if (HotkeyParser.IsRisky(normalized) && _pendingRisky != normalized)
        {
            _pendingRisky = normalized;
            ShowHint($"{normalized} is used for typing or editing. While CaptionBridge runs, it would stop working in other apps. " +
                     "Press it again to use it anyway, or press another key.");
            return;
        }
        Finish(normalized);
    }

    private void Finish(string result)
    {
        Result = result;
        DialogResult = true;
    }

    private static string ModText(ModifierKeys mods) =>
        (mods.HasFlag(ModifierKeys.Control) ? "Ctrl+" : "") +
        (mods.HasFlag(ModifierKeys.Alt) ? "Alt+" : "") +
        (mods.HasFlag(ModifierKeys.Shift) ? "Shift+" : "") +
        (mods.HasFlag(ModifierKeys.Windows) ? "Win+" : "");

    /// <summary>
    /// Name stored for a key: F1–F24, A–Z, 0–9 (top row), or the Windows key name ("PageUp", "NumPad5", "Pause").
    /// Returns null for modifiers on their own and keys that can't be registered.
    /// </summary>
    private static string? KeyName(Key key)
    {
        if (key is >= Key.F1 and <= Key.F24) return "F" + (key - Key.F1 + 1);
        if (key is >= Key.A and <= Key.Z) return ((char)('A' + (key - Key.A))).ToString();
        if (key is >= Key.D0 and <= Key.D9) return ((char)('0' + (key - Key.D0))).ToString();
        if (key is Key.None or Key.LeftCtrl or Key.RightCtrl or Key.LeftAlt or Key.RightAlt or Key.LeftShift
                or Key.RightShift or Key.LWin or Key.RWin or Key.System or Key.ImeProcessed or Key.DeadCharProcessed)
            return null;
        if (KeyInterop.VirtualKeyFromKey(key) == 0) return null;
        // Friendlier names for keys that have two names in Windows.
        return key switch
        {
            Key.Enter => "Enter",
            Key.PageUp => "PageUp",
            Key.PageDown => "PageDown",
            Key.CapsLock => "CapsLock",
            Key.PrintScreen => "PrintScreen",
            Key.Back => "Back",
            _ => key.ToString(),
        };
    }
}
