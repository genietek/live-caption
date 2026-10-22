using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Shapes;
using System.Windows.Threading;
using CaptionBridge.Core;
using Microsoft.Win32;
using WinForms = System.Windows.Forms;

namespace CaptionBridge;

/// <summary>Main window, built in code (no XAML) to keep the app small and buildable anywhere.</summary>
internal sealed class MainWindow : Window
{
    private AppSettings _settings;
    private readonly CaptionWorker _worker;
    private HotkeyManager? _hotkeys;
    private WinForms.NotifyIcon? _tray;
    private bool _exiting;
    private WorkerStatus _status = new();

    // status widgets
    private readonly TextBlock _captionStatus = new() { TextWrapping = TextWrapping.Wrap };
    private readonly TextBlock _insertState = new() { FontWeight = FontWeights.SemiBold };
    private readonly TextBlock _target = new() { TextWrapping = TextWrapping.Wrap };
    private readonly TextBlock _targetStatus = new() { TextWrapping = TextWrapping.Wrap };
    private readonly TextBlock _lock = new() { TextWrapping = TextWrapping.Wrap };
    private readonly TextBlock _lastInsert = new() { TextWrapping = TextWrapping.Wrap };
    private readonly TextBlock _pending = new() { TextWrapping = TextWrapping.Wrap, FontStyle = FontStyles.Italic, Foreground = Brushes.Gray, MinHeight = 20 };
    private readonly TextBox _transcript = new()
    {
        IsReadOnly = true, TextWrapping = TextWrapping.Wrap, AcceptsReturn = true, MinHeight = 120,
        VerticalScrollBarVisibility = ScrollBarVisibility.Auto, FontSize = 14,
    };
    private readonly ListBox _messages = new() { Height = 110, FontSize = 11 };
    private readonly TextBlock _stats = new() { Foreground = Brushes.DimGray, FontSize = 11, TextWrapping = TextWrapping.Wrap };
    private readonly Ellipse _dot = new() { Width = 12, Height = 12, Margin = new Thickness(0, 0, 6, 0) };

    private readonly Button _captureBtn = new() { Padding = new Thickness(10, 4, 10, 4), Margin = new Thickness(0, 0, 6, 6) };
    private readonly Button _autoBtn = new() { Padding = new Thickness(10, 4, 10, 4), Margin = new Thickness(0, 0, 6, 6) };
    private readonly Button _pauseBtn = new() { Padding = new Thickness(10, 4, 10, 4), Margin = new Thickness(0, 0, 6, 6) };
    private readonly Button _lockBtn = new() { Padding = new Thickness(10, 4, 10, 4), Margin = new Thickness(0, 0, 6, 6) };

    // perf
    private readonly DispatcherTimer _perfTimer = new() { Interval = TimeSpan.FromSeconds(2) };
    private TimeSpan _lastCpu;
    private DateTime _lastCpuAt = DateTime.UtcNow;

    public MainWindow(AppSettings settings)
    {
        _settings = settings;
        Title = "CaptionBridge – Live Captions to textbox";
        Width = 580;
        Height = 760;
        MinWidth = 440;
        MinHeight = 520;
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

        _perfTimer.Tick += (_, _) => UpdatePerf();
        _perfTimer.Start();

        SystemEvents.SessionSwitch += OnSessionSwitch;
        SystemEvents.PowerModeChanged += OnPowerModeChanged;

        CreateTray();
        _worker.Start();
        if (settings.StartCaptureOnLaunch) _worker.SetCapture(true);
        if (settings.AutoInsertOnStartup) _worker.SetAutoInsert(true);
    }

    // ---------------------------------------------------------------- layout

    private UIElement BuildLayout()
    {
        var root = new DockPanel { Margin = new Thickness(14) };

        // Emergency stop, always visible at the top
        var stop = new Button
        {
            Content = "■  STOP INSERTION",
            FontSize = 16,
            FontWeight = FontWeights.Bold,
            Foreground = Brushes.White,
            Background = new SolidColorBrush(Color.FromRgb(0xC4, 0x2B, 0x1C)),
            Padding = new Thickness(12, 8, 12, 8),
            Margin = new Thickness(0, 0, 0, 10),
            ToolTip = $"Disables Auto Insert immediately and discards queued captions ({_settings.HotkeyStop})",
        };
        stop.Click += (_, _) => _worker.EmergencyStop();
        DockPanel.SetDock(stop, Dock.Top);
        root.Children.Add(stop);

        // Status grid
        var grid = new Grid { Margin = new Thickness(0, 0, 0, 8) };
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        var captionRow = new StackPanel { Orientation = Orientation.Horizontal };
        captionRow.Children.Add(_dot);
        captionRow.Children.Add(_captionStatus);
        _captionStatus.MaxWidth = 420;
        AddRow(grid, "Live Captions", captionRow);
        AddRow(grid, "Auto Insert", _insertState);
        AddRow(grid, "Focused", _target);
        AddRow(grid, "Target status", _targetStatus);
        AddRow(grid, "Target lock", _lock);
        AddRow(grid, "Last insertion", _lastInsert);
        DockPanel.SetDock(grid, Dock.Top);
        root.Children.Add(grid);

        // Buttons
        var buttons = new WrapPanel { Margin = new Thickness(0, 0, 0, 6) };
        _captureBtn.Click += (_, _) => _worker.SetCapture(!_status.Capturing);
        _autoBtn.Click += (_, _) => _worker.SetAutoInsert(!_status.AutoInsert);
        _pauseBtn.Click += (_, _) => _worker.SetManualPause(!_status.Paused);
        _lockBtn.Click += (_, _) =>
        {
            if (_status.LockedTarget != null || _status.LockArmed) _worker.Unlock();
            else _worker.ArmLock();
        };
        var launch = new Button { Content = "Launch Live Captions", Padding = new Thickness(10, 4, 10, 4), Margin = new Thickness(0, 0, 6, 6), ToolTip = "Sends Win+Ctrl+L (toggles Live Captions)" };
        launch.Click += (_, _) => LiveCaptionsSource.LaunchWithShortcut();
        foreach (var b in new[] { _captureBtn, _autoBtn, _pauseBtn, _lockBtn, launch }) buttons.Children.Add(b);
        DockPanel.SetDock(buttons, Dock.Top);
        root.Children.Add(buttons);

        // Bottom: stats, messages, settings
        DockPanel.SetDock(_stats, Dock.Bottom);
        root.Children.Add(_stats);

        var settingsExpander = new Expander { Header = "Settings", Content = BuildSettings(), Margin = new Thickness(0, 6, 0, 6) };
        DockPanel.SetDock(settingsExpander, Dock.Bottom);
        root.Children.Add(settingsExpander);

        var msgPanel = new StackPanel();
        msgPanel.Children.Add(Label("Messages"));
        msgPanel.Children.Add(_messages);
        DockPanel.SetDock(msgPanel, Dock.Bottom);
        root.Children.Add(msgPanel);

        // Center: caption preview
        var center = new DockPanel();
        var listening = new StackPanel();
        listening.Children.Add(Label("Listening (not yet stable)"));
        listening.Children.Add(_pending);
        listening.Children.Add(Label("Captions (committed text; select to copy)"));
        DockPanel.SetDock(listening, Dock.Top);
        center.Children.Add(listening);
        center.Children.Add(_transcript);
        root.Children.Add(center);

        return root;
    }

    private static void AddRow(Grid g, string label, UIElement value)
    {
        int r = g.RowDefinitions.Count;
        g.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        var l = new TextBlock { Text = label, Foreground = Brushes.DimGray, Margin = new Thickness(0, 2, 12, 2) };
        Grid.SetRow(l, r);
        g.Children.Add(l);
        if (value is FrameworkElement fe) fe.Margin = new Thickness(0, 2, 0, 2);
        Grid.SetRow(value, r);
        Grid.SetColumn(value, 1);
        g.Children.Add(value);
    }

    private static TextBlock Label(string text) =>
        new() { Text = text, FontWeight = FontWeights.SemiBold, Margin = new Thickness(0, 6, 0, 2) };

    private UIElement BuildSettings()
    {
        var s = _settings;
        var panel = new StackPanel { Margin = new Thickness(8, 4, 0, 4) };

        var autoOnStart = Check("Enable Auto Insert when the app starts", s.AutoInsertOnStartup);
        var captureOnStart = Check("Start capturing captions when the app starts", s.StartCaptureOnLaunch);
        var delay = Number("Stabilization delay (ms): how long a word must stay unchanged before insertion", s.StabilizationDelayMs, panel);
        var poll = Number("Caption polling interval (ms)", s.PollIntervalMs, panel);
        var mode = Combo("Insert", new[] { "Stable phrases (lower delay)", "Complete sentences only (fewer corrections)" }, (int)s.CommitMode, panel);
        var flush = Number("In sentence mode, insert an unfinished sentence after this much silence (ms)", s.SilenceFlushMs, panel);
        var method = Combo("Insertion method", new[] { "Direct typing (Unicode key events)", "Clipboard paste (Ctrl+V, clipboard restored)" }, (int)s.InsertionMethod, panel);
        var restore = Number("Clipboard restore delay (ms)", s.ClipboardRestoreDelayMs, panel);
        var autoPause = Check("Pause automatically when no valid textbox is focused (drop captions meanwhile)", s.AutoPauseOnFocusLoss);
        var verify = Check("Verify insertion when the field exposes its text", s.VerifyInsertion);
        var tray = Check("Minimize to the system tray", s.MinimizeToTray);
        var startMin = Check("Start minimized", s.StartMinimized);
        var hotkeysOn = Check("Enable global hotkeys", s.HotkeysEnabled);
        var hkToggle = Text("Hotkey: toggle Auto Insert", s.HotkeyToggleInsert, panel);
        var hkPause = Text("Hotkey: pause / resume", s.HotkeyPauseResume, panel);
        var hkStop = Text("Hotkey: stop insertion immediately", s.HotkeyStop, panel);

        foreach (var c in new UIElement[] { autoOnStart, captureOnStart, autoPause, verify, tray, startMin, hotkeysOn })
            panel.Children.Add(c);

        var save = new Button { Content = "Save settings", Padding = new Thickness(10, 4, 10, 4), Margin = new Thickness(0, 8, 0, 4), HorizontalAlignment = HorizontalAlignment.Left };
        save.Click += (_, _) =>
        {
            var n = new AppSettings
            {
                AutoInsertOnStartup = autoOnStart.IsChecked == true,
                StartCaptureOnLaunch = captureOnStart.IsChecked == true,
                StabilizationDelayMs = ParseInt(delay.Text, s.StabilizationDelayMs),
                PollIntervalMs = ParseInt(poll.Text, s.PollIntervalMs),
                CommitMode = (CommitMode)Math.Max(0, mode.SelectedIndex),
                SilenceFlushMs = ParseInt(flush.Text, s.SilenceFlushMs),
                InsertionMethod = (InsertionMethod)Math.Max(0, method.SelectedIndex),
                ClipboardRestoreDelayMs = ParseInt(restore.Text, s.ClipboardRestoreDelayMs),
                AutoPauseOnFocusLoss = autoPause.IsChecked == true,
                VerifyInsertion = verify.IsChecked == true,
                MinimizeToTray = tray.IsChecked == true,
                StartMinimized = startMin.IsChecked == true,
                HotkeysEnabled = hotkeysOn.IsChecked == true,
                HotkeyToggleInsert = hkToggle.Text.Trim(),
                HotkeyPauseResume = hkPause.Text.Trim(),
                HotkeyStop = hkStop.Text.Trim(),
            }.Normalize();
            _settings = n;
            _worker.ApplySettings(n);
            RegisterHotkeys();
            var err = SettingsStore.Save(n);
            AddMessage(err == null ? $"Settings saved to {SettingsStore.FilePath}" : $"Could not save settings: {err}");
        };
        panel.Children.Add(save);
        panel.Children.Add(new TextBlock
        {
            TextWrapping = TextWrapping.Wrap, Foreground = Brushes.DimGray, FontSize = 11,
            Text = "Caption language (English, Spanish, …) is chosen in Live Captions itself (gear icon > Language). " +
                   "This app inserts exactly what Live Captions shows. It does not detect, translate or change the language, " +
                   "and a language only works if Live Captions offers it on this PC.",
        });
        return new ScrollViewer { Content = panel, MaxHeight = 300, VerticalScrollBarVisibility = ScrollBarVisibility.Auto };
    }

    private static CheckBox Check(string text, bool value) =>
        new() { Content = text, IsChecked = value, Margin = new Thickness(0, 3, 0, 3) };

    private static TextBox Number(string label, int value, Panel panel) => Text(label, value.ToString(), panel);

    private static TextBox Text(string label, string value, Panel panel)
    {
        var row = new DockPanel { Margin = new Thickness(0, 2, 0, 2) };
        var box = new TextBox { Text = value, Width = 140, Margin = new Thickness(8, 0, 0, 0) };
        DockPanel.SetDock(box, Dock.Right);
        row.Children.Add(box);
        row.Children.Add(new TextBlock { Text = label, TextWrapping = TextWrapping.Wrap, VerticalAlignment = VerticalAlignment.Center });
        panel.Children.Add(row);
        return box;
    }

    private static ComboBox Combo(string label, string[] items, int selected, Panel panel)
    {
        var row = new DockPanel { Margin = new Thickness(0, 2, 0, 2) };
        var box = new ComboBox { ItemsSource = items, SelectedIndex = selected, Width = 300, Margin = new Thickness(8, 0, 0, 0) };
        DockPanel.SetDock(box, Dock.Right);
        row.Children.Add(box);
        row.Children.Add(new TextBlock { Text = label, VerticalAlignment = VerticalAlignment.Center });
        panel.Children.Add(row);
        return box;
    }

    private static int ParseInt(string text, int fallback) => int.TryParse(text.Trim(), out var v) ? v : fallback;

    // ---------------------------------------------------------------- rendering

    private void Render(WorkerStatus s)
    {
        _status = s;
        _dot.Fill = s.Connected ? Brushes.SeaGreen : (s.Capturing ? Brushes.Goldenrod : Brushes.Gray);
        _captionStatus.Text = s.CaptionStatus;
        _insertState.Text = !s.AutoInsert ? "OFF" : s.Paused ? "ON (paused)" : "ON";
        _insertState.Foreground = s.AutoInsert && !s.Paused ? Brushes.SeaGreen : Brushes.DimGray;
        _target.Text = s.Target;
        _targetStatus.Text = s.GateReason;
        _targetStatus.Foreground = s.Gate == GateStatus.Ready ? Brushes.SeaGreen : Brushes.DimGray;
        _lock.Text = s.LockedTarget != null ? $"Locked to {s.LockedTarget}" : s.LockArmed ? "Waiting: click into the textbox to lock" : "Not locked (follows focus)";
        _lastInsert.Text = string.IsNullOrEmpty(s.LastInsert) ? "—" : s.LastInsert;
        _lastInsert.Foreground = s.LastInsert.StartsWith("NOT", StringComparison.Ordinal) ? Brushes.Firebrick : Brushes.Black;
        _pending.Text = s.Pending;

        if (_transcript.Text != s.Transcript)
        {
            bool atEnd = _transcript.VerticalOffset + _transcript.ViewportHeight >= _transcript.ExtentHeight - 4;
            if (_transcript.SelectionLength == 0)
            {
                _transcript.Text = s.Transcript;
                if (atEnd) _transcript.ScrollToEnd();
            }
        }

        _captureBtn.Content = s.Capturing ? "Stop capture" : "Start capture";
        _autoBtn.Content = s.AutoInsert ? "Disable Auto Insert" : "Enable Auto Insert";
        _pauseBtn.Content = s.Paused ? "Resume" : "Pause";
        _pauseBtn.IsEnabled = s.AutoInsert;
        _lockBtn.Content = s.LockedTarget != null || s.LockArmed ? "Unlock target" : "Lock to next textbox";

        if (_tray != null)
            _tray.Text = Shorten($"CaptionBridge: {(s.AutoInsert ? (s.Paused ? "paused" : "inserting") : "Auto Insert off")}", 63);
    }

    private void UpdatePerf()
    {
        using var p = Process.GetCurrentProcess();
        var now = DateTime.UtcNow;
        var cpu = p.TotalProcessorTime;
        double wall = (now - _lastCpuAt).TotalSeconds;
        double pct = wall > 0 ? 100.0 * (cpu - _lastCpu).TotalSeconds / wall / Environment.ProcessorCount : 0;
        _lastCpu = cpu;
        _lastCpuAt = now;
        var s = _status;
        _stats.Text =
            $"Measured live: CPU {pct:F1}% of all cores · RAM {p.WorkingSet64 / 1048576.0:F0} MB (peak {p.PeakWorkingSet64 / 1048576.0:F0} MB) · " +
            $"caption read {s.AvgReadMs:F1} ms avg · discovery {s.LastDiscoveryMs:F0} ms · last insert {s.LastInsertMs:F0} ms · " +
            $"inserted {s.InsertedChars} chars, {s.FailedInserts} failed · corrections ignored {s.CorrectionsIgnored} · resets {s.Resets}";
    }

    private void AddMessage(string m)
    {
        _messages.Items.Insert(0, $"{DateTime.Now:HH:mm:ss}  {m}");
        while (_messages.Items.Count > 100) _messages.Items.RemoveAt(_messages.Items.Count - 1);
    }

    private static string Shorten(string s, int max) => s.Length <= max ? s : s[..max];

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
            case HotkeyAction.ToggleInsert: _worker.ToggleAutoInsert(); break;
            case HotkeyAction.PauseResume: _worker.TogglePause(); break;
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
        menu.Items.Add("Enable / disable Auto Insert", null, (_, _) => _worker.ToggleAutoInsert());
        menu.Items.Add("STOP insertion", null, (_, _) => _worker.EmergencyStop());
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
        _perfTimer.Stop();
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
