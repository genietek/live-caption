using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using CaptionBridge.Core;

namespace CaptionBridge;

/// <summary>
/// Small "press a key" dialog for choosing a hotkey. It only listens while it is open and focused;
/// nothing is logged. Esc cancels, Backspace or Delete turns the hotkey off.
/// </summary>
internal sealed class HotkeyCaptureDialog : Window
{
    private readonly TextBlock _keyText = new()
    {
        FontSize = 20, FontWeight = FontWeights.SemiBold, HorizontalAlignment = HorizontalAlignment.Center,
        Margin = new Thickness(0, 10, 0, 6),
    };
    private readonly TextBlock _hint = new() { TextWrapping = TextWrapping.Wrap, FontSize = 12, Foreground = Brushes.DimGray };

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
        _hint.Text = error ?? "Use an F-key (F1 to F24), or two of Ctrl/Alt/Shift plus a letter or digit. Esc cancels. Backspace turns this hotkey off.";
        _hint.Foreground = error == null ? Brushes.DimGray : Brushes.Firebrick;
    }

    private void OnKeyDown(object sender, KeyEventArgs e)
    {
        e.Handled = true; // keep Alt/F10 from opening the system menu, and Space/Enter from pressing buttons
        var key = e.Key switch { Key.System => e.SystemKey, Key.ImeProcessed => e.ImeProcessedKey, _ => e.Key };
        var mods = Keyboard.Modifiers;

        if (mods == ModifierKeys.None && key == Key.Escape) { Close(); return; }
        if (mods == ModifierKeys.None && key is Key.Back or Key.Delete) { Finish(""); return; }

        string? name = KeyName(key);
        if (name == null)
        {
            // A modifier on its own: show what is held so far and keep waiting.
            if (key is Key.LeftCtrl or Key.RightCtrl or Key.LeftAlt or Key.RightAlt or Key.LeftShift or Key.RightShift or Key.LWin or Key.RWin)
            {
                _keyText.Text = ModText(mods) + "…";
                return;
            }
            ShowHint($"{key} can't be used. Pick an F-key, or Ctrl/Alt/Shift plus a letter or digit.");
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
            ShowHint(name.StartsWith('F') && name.Length > 1
                ? "Alt+F4 closes windows. Pick another key."
                : "A letter or digit needs two of Ctrl/Alt/Shift (for example Ctrl+Alt+I), so normal typing is never taken.");
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

    /// <summary>F1–F24, A–Z and 0–9 (top row or number pad). Anything else returns null.</summary>
    private static string? KeyName(Key key)
    {
        if (key is >= Key.F1 and <= Key.F24) return "F" + (key - Key.F1 + 1);
        if (key is >= Key.A and <= Key.Z) return ((char)('A' + (key - Key.A))).ToString();
        if (key is >= Key.D0 and <= Key.D9) return ((char)('0' + (key - Key.D0))).ToString();
        if (key is >= Key.NumPad0 and <= Key.NumPad9) return ((char)('0' + (key - Key.NumPad0))).ToString();
        return null;
    }
}
