using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Shapes;
using CaptionBridge.Core;
using Microsoft.Win32;
using WinForms = System.Windows.Forms;

namespace CaptionBridge;

/// <summary>
/// Main window, built in code (no XAML). Deliberately small: connection status, one Start/Stop button
/// (F8 / F9), where text is going, the caption preview, and a collapsed Settings section.
/// </summary>
internal sealed class MainWindow : Window
{
    private static readonly Brush Green = new SolidColorBrush(Color.FromRgb(0x1E, 0x8E, 0x3E));
    private static readonly Brush Red = new SolidColorBrush(Color.FromRgb(0xC4, 0x2B, 0x1C));
    private static readonly Brush Muted = Brushes.DimGray;

    private AppSettings _settings;
    private readonly CaptionWorker _worker;
    private HotkeyManager? _hotkeys;
    private WinForms.NotifyIcon? _tray;
    private bool _exiting;
    private WorkerStatus _status = new();
    private readonly List<string> _log = new(); // newest first, for Copy diagnostics

    private readonly Ellipse _dot = new() { Width = 10, Height = 10, Margin = new Thickness(0, 0, 8, 0), VerticalAlignment = VerticalAlignment.Center };
    private readonly TextBlock _captionStatus = new() { TextWrapping = TextWrapping.Wrap };
    private readonly Button _mainBtn = new()
    {
        FontSize = 16, FontWeight = FontWeights.SemiBold, Foreground = Brushes.White,
        Padding = new Thickness(12, 10, 12, 10), Margin = new Thickness(0, 12, 0, 8), BorderThickness = new Thickness(0),
    };
    private readonly TextBlock _target = new() { TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 0, 0, 8) };
    private readonly TextBox _transcript = new()
    {
        IsReadOnly = true, TextWrapping = TextWrapping.Wrap, FontSize = 14, Padding = new Thickness(4),
        VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
    };
    private readonly TextBlock _pending = new() { TextWrapping = TextWrapping.Wrap, FontStyle = FontStyles.Italic, Foreground = Brushes.Gray, Margin = new Thickness(0, 4, 0, 0) };
    private readonly TextBlock _message = new() { TextWrapping = TextWrapping.Wrap, Foreground = Muted, FontSize = 12, Margin = new Thickness(0, 6, 0, 0) };

    public MainWindow(AppSettings settings)
    {
        _settings = settings;
        Title = "CaptionBridge";
        Width = 460;
        Height = 560;
        MinWidth = 360;
        MinHeight = 400;
        Background = SystemColors.WindowBrush;
        FontFamily = new FontFamily("Segoe UI");

        _worker = new CaptionWorker(settings, new WpfClipboardAccess(Dispatcher));
        _worker.StatusChanged += s => Dispatcher.BeginInvoke(() => Render(s));
        _worker.Message += m => Dispatcher.BeginInvoke(() => AddMessage(m));

        Content = BuildLayout();
        Render(_status);

        SourceInitialized += (_, _) => RegisterHotkeys();
        StateChanged += (_, _) =>
        {
            if (WindowState == WindowState.Minimized && _settings.MinimizeToTray) Hide();
        };
        Closing += (_, _) => Shutdown();

        SystemEvents.SessionSwitch += OnSessionSwitch;
        SystemEvents.PowerModeChanged += OnPowerModeChanged;

        CreateTray();
        _worker.Start();
        _worker.SetCapture(true);
        if (settings.AutoInsertOnStartup) _worker.StartAutoInsert();
    }

    // ---------------------------------------------------------------- layout

    private UIElement BuildLayout()
    {
        var root = new DockPanel { Margin = new Thickness(16) };

        var top = new StackPanel();
        var statusRow = new DockPanel();
        statusRow.Children.Add(_dot);
        statusRow.Children.Add(_captionStatus);
        top.Children.Add(statusRow);
        _mainBtn.Click += (_, _) =>
        {
            if (_status.AutoInsert) _worker.EmergencyStop();
            else _worker.StartAutoInsert();
        };
        top.Children.Add(_mainBtn);
        top.Children.Add(_target);
        DockPanel.SetDock(top, Dock.Top);
        root.Children.Add(top);

        var bottom = new StackPanel();
        bottom.Children.Add(_pending);
        bottom.Children.Add(_message);
        var links = new WrapPanel { Margin = new Thickness(0, 8, 0, 0) };
        links.Children.Add(SmallButton("Open Live Captions", "Sends Win+Ctrl+L", LiveCaptionsSource.LaunchWithShortcut));
        links.Children.Add(SmallButton("Copy diagnostics", "Copies the app state to paste into a bug report", CopyDiagnostics));
        bottom.Children.Add(links);
        bottom.Children.Add(new Expander { Header = "Settings", Content = BuildSettings(), Margin = new Thickness(0, 6, 0, 0) });
        DockPanel.SetDock(bottom, Dock.Bottom);
        root.Children.Add(bottom);

        root.Children.Add(_transcript);
        return root;
    }

    private static Button SmallButton(string text, string tip, Action onClick)
    {
        var b = new Button { Content = text, ToolTip = tip, Padding = new Thickness(8, 3, 8, 3), Margin = new Thickness(0, 0, 6, 0) };
        b.Click += (_, _) => onClick();
        return b;
    }

    private UIElement BuildSettings()
    {
        var s = _settings;
        var panel = new StackPanel { Margin = new Thickness(4, 6, 0, 4) };

        var start = Text("Start hotkey", s.HotkeyStart, panel);
        var stop = Text("Stop hotkey", s.HotkeyStop, panel);
        var delay = Text("Wait before typing a word (ms)", s.StabilizationDelayMs.ToString(), panel);
        var mode = Combo("Type", new[] { "Words as soon as stable", "Whole sentences" }, (int)s.CommitMode, panel);
        var method = Combo("Method", new[] { "Typing", "Clipboard paste" }, (int)s.InsertionMethod, panel);
        var tray = Check("Minimize to tray", s.MinimizeToTray, panel);
        var startMin = Check("Start minimized", s.StartMinimized, panel);
        var autoStart = Check("Start Auto Insert when the app opens", s.AutoInsertOnStartup, panel);

        var save = new Button { Content = "Save", Padding = new Thickness(14, 3, 14, 3), Margin = new Thickness(0, 6, 0, 0), HorizontalAlignment = HorizontalAlignment.Left };
        save.Click += (_, _) =>
        {
            var n = AppSettings.FromJson(_settings.ToJson()); // keep advanced values from the settings file
            n.HotkeyStart = start.Text.Trim();
            n.HotkeyStop = stop.Text.Trim();
            n.StabilizationDelayMs = int.TryParse(delay.Text.Trim(), out var d) ? d : s.StabilizationDelayMs;
            n.CommitMode = (CommitMode)Math.Max(0, mode.SelectedIndex);
            n.InsertionMethod = (InsertionMethod)Math.Max(0, method.SelectedIndex);
            n.MinimizeToTray = tray.IsChecked == true;
            n.StartMinimized = startMin.IsChecked == true;
            n.AutoInsertOnStartup = autoStart.IsChecked == true;
            n.Normalize();
            _settings = n;
            _worker.ApplySettings(n);
            RegisterHotkeys();
            Render(_status);
            var err = SettingsStore.Save(n);
            AddMessage(err == null ? "Settings saved" : $"Could not save settings: {err}");
        };
        panel.Children.Add(save);
        return panel;
    }

    private static TextBox Text(string label, string value, Panel panel)
    {
        var row = new DockPanel { Margin = new Thickness(0, 2, 0, 2) };
        var box = new TextBox { Text = value, Width = 150 };
        DockPanel.SetDock(box, Dock.Right);
        row.Children.Add(box);
        row.Children.Add(new TextBlock { Text = label, VerticalAlignment = VerticalAlignment.Center });
        panel.Children.Add(row);
        return box;
    }

    private static ComboBox Combo(string label, string[] items, int selected, Panel panel)
    {
        var row = new DockPanel { Margin = new Thickness(0, 2, 0, 2) };
        var box = new ComboBox { ItemsSource = items, SelectedIndex = selected, Width = 150 };
        DockPanel.SetDock(box, Dock.Right);
        row.Children.Add(box);
        row.Children.Add(new TextBlock { Text = label, VerticalAlignment = VerticalAlignment.Center });
        panel.Children.Add(row);
        return box;
    }

    private static CheckBox Check(string text, bool value, Panel panel)
    {
        var c = new CheckBox { Content = text, IsChecked = value, Margin = new Thickness(0, 3, 0, 3) };
        panel.Children.Add(c);
        return c;
    }

    // ---------------------------------------------------------------- rendering

    private void Render(WorkerStatus s)
    {
        _status = s;
        _dot.Fill = s.Connected ? Green : Brushes.Goldenrod;
        _captionStatus.Text = s.Connected ? "Live Captions connected" : s.CaptionStatus;

        string startKey = string.IsNullOrWhiteSpace(_settings.HotkeyStart) ? "" : $"  ({_settings.HotkeyStart})";
        string stopKey = string.IsNullOrWhiteSpace(_settings.HotkeyStop) ? "" : $"  ({_settings.HotkeyStop})";
        _mainBtn.Content = s.AutoInsert ? $"■  Stop Auto Insert{stopKey}" : $"▶  Start Auto Insert{startKey}";
        _mainBtn.Background = s.AutoInsert ? Red : Green;

        if (!s.AutoInsert)
        {
            _target.Text = "Auto Insert is off.";
            _target.Foreground = Muted;
        }
        else if (s.Gate == GateStatus.Ready)
        {
            _target.Text = $"Typing into {s.Target}";
            _target.Foreground = Green;
        }
        else
        {
            _target.Text = $"Waiting: {s.GateReason}. Click into a textbox.";
            _target.Foreground = Muted;
        }

        _pending.Text = s.Pending.Length > 0 ? "… " + s.Pending : "";

        if (_transcript.Text != s.Transcript && _transcript.SelectionLength == 0)
        {
            bool atEnd = _transcript.VerticalOffset + _transcript.ViewportHeight >= _transcript.ExtentHeight - 4;
            _transcript.Text = s.Transcript;
            if (atEnd) _transcript.ScrollToEnd();
        }

        if (s.LastInsert.StartsWith("NOT", StringComparison.Ordinal))
        {
            _message.Text = s.LastInsert;
            _message.Foreground = Red;
        }

        if (_tray != null)
            _tray.Text = s.AutoInsert ? "CaptionBridge: Auto Insert on" : "CaptionBridge: Auto Insert off";
    }

    private void CopyDiagnostics()
    {
        var s = _status;
        using var p = Process.GetCurrentProcess();
        var sb = new System.Text.StringBuilder();
        sb.AppendLine($"CaptionBridge {typeof(MainWindow).Assembly.GetName().Version} diagnostics, {DateTime.Now:yyyy-MM-dd HH:mm:ss}");
        sb.AppendLine($"Windows: {Environment.OSVersion.VersionString}, {(Environment.Is64BitProcess ? "x64" : "x86")}, cores {Environment.ProcessorCount}");
        sb.AppendLine($"Memory: {p.WorkingSet64 / 1048576.0:F0} MB (peak {p.PeakWorkingSet64 / 1048576.0:F0} MB), CPU time {p.TotalProcessorTime.TotalSeconds:F1} s");
        sb.AppendLine($"Capturing: {s.Capturing}, connected: {s.Connected}, caption status: {s.CaptionStatus}");
        sb.AppendLine($"Caption text changes seen: {s.SnapshotChanges}, committed chars: {s.CommittedChars}, avg read {s.AvgReadMs:F1} ms, discovery {s.LastDiscoveryMs:F0} ms");
        sb.AppendLine($"Last caption text: \"{s.CaptionSample.Replace("\r", " ").Replace("\n", " ")}\"");
        sb.AppendLine($"Pending: \"{s.Pending}\"");
        sb.AppendLine($"Auto Insert: {s.AutoInsert}, paused: {s.Paused}, gate: {s.Gate}, reason: {s.GateReason}");
        sb.AppendLine($"Focused: {s.TargetDetails}");
        sb.AppendLine($"Inserted chars: {s.InsertedChars}, failed inserts: {s.FailedInserts}, dropped chars: {s.DroppedChars}, last insert {s.LastInsertMs:F0} ms: {s.LastInsert}");
        sb.AppendLine($"Corrections ignored: {s.CorrectionsIgnored}, resets: {s.Resets}");
        sb.AppendLine("Settings: " + _settings.ToJson().Replace(Environment.NewLine, " "));
        sb.AppendLine("Recent messages (newest first):");
        for (int i = 0; i < Math.Min(30, _log.Count); i++) sb.AppendLine("  " + _log[i]);
        try
        {
            Clipboard.SetText(sb.ToString());
            AddMessage("Diagnostics copied. Paste them into the chat.");
        }
        catch (System.Runtime.InteropServices.COMException ex)
        {
            AddMessage($"Could not copy diagnostics: {ex.Message}");
        }
    }

    private void AddMessage(string m)
    {
        _log.Insert(0, $"{DateTime.Now:HH:mm:ss}  {m}");
        if (_log.Count > 100) _log.RemoveAt(_log.Count - 1);
        _message.Text = m;
        _message.Foreground = Muted;
    }

    // ---------------------------------------------------------------- hotkeys, tray, lifecycle

    private void RegisterHotkeys()
    {
        var hwnd = new WindowInteropHelper(this).EnsureHandle();
        if (_hotkeys == null)
        {
            _hotkeys = new HotkeyManager(hwnd);
            HwndSource.FromHwnd(hwnd)?.AddHook(WndProc);
        }
        foreach (var problem in _hotkeys.Apply(_settings)) AddMessage(problem);
    }

    private IntPtr WndProc(IntPtr hwnd, int msg, IntPtr wParam, IntPtr lParam, ref bool handled)
    {
        if (msg != NativeMethods.WM_HOTKEY) return IntPtr.Zero;
        switch ((HotkeyAction)wParam.ToInt32())
        {
            case HotkeyAction.Start: _worker.StartAutoInsert(); break;
            case HotkeyAction.Stop: _worker.EmergencyStop(); break;
        }
        handled = true;
        return IntPtr.Zero;
    }

    private void CreateTray()
    {
        _tray = new WinForms.NotifyIcon
        {
            Icon = System.Drawing.SystemIcons.Application,
            Text = "CaptionBridge",
            Visible = true,
        };
        var menu = new WinForms.ContextMenuStrip();
        menu.Items.Add("Show", null, (_, _) => Dispatcher.BeginInvoke(ShowFromTray));
        menu.Items.Add("Start Auto Insert", null, (_, _) => _worker.StartAutoInsert());
        menu.Items.Add("Stop Auto Insert", null, (_, _) => _worker.EmergencyStop());
        menu.Items.Add(new WinForms.ToolStripSeparator());
        menu.Items.Add("Exit", null, (_, _) => Dispatcher.BeginInvoke(Close));
        _tray.ContextMenuStrip = menu;
        _tray.DoubleClick += (_, _) => Dispatcher.BeginInvoke(ShowFromTray);
    }

    public void ShowFromTray()
    {
        Show();
        if (WindowState == WindowState.Minimized) WindowState = WindowState.Normal;
        Activate();
    }

    private void OnSessionSwitch(object? sender, SessionSwitchEventArgs e)
    {
        if (e.Reason is SessionSwitchReason.SessionLock or SessionSwitchReason.ConsoleDisconnect or SessionSwitchReason.RemoteDisconnect)
            _worker.SetSystemPause(true, "Windows session locked: insertion paused");
        else if (e.Reason is SessionSwitchReason.SessionUnlock or SessionSwitchReason.ConsoleConnect or SessionSwitchReason.RemoteConnect)
            _worker.SetSystemPause(false, "Windows session unlocked: insertion resumed (older captions skipped)");
    }

    private void OnPowerModeChanged(object? sender, PowerModeChangedEventArgs e)
    {
        if (e.Mode == PowerModes.Suspend) _worker.SetSystemPause(true, "System suspending: insertion paused");
        else if (e.Mode == PowerModes.Resume) _worker.SetSystemPause(false, "System resumed: insertion resumed (older captions skipped)");
    }

    private void Shutdown()
    {
        if (_exiting) return;
        _exiting = true;
        _worker.EmergencyStop();
        SystemEvents.SessionSwitch -= OnSessionSwitch;
        SystemEvents.PowerModeChanged -= OnPowerModeChanged;
        _hotkeys?.Dispose();
        _worker.Dispose();
        if (_tray != null)
        {
            _tray.Visible = false;
            _tray.Dispose();
        }
        Application.Current?.Shutdown();
    }
}
