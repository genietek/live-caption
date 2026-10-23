using System;
using System.Threading;
using System.Windows;
using System.Windows.Interop;

namespace CaptionBridge;

internal static class Program
{
    [STAThread]
    private static int Main()
    {
        using var mutex = new Mutex(true, @"Local\CaptionBridge.SingleInstance", out bool firstInstance);
        if (!firstInstance)
        {
            MessageBox.Show("CaptionBridge is already running. Look for its icon in the system tray.", "CaptionBridge");
            return 0;
        }

        var app = new Application { ShutdownMode = ShutdownMode.OnExplicitShutdown };
        app.DispatcherUnhandledException += (_, e) =>
        {
            MessageBox.Show($"Unexpected error: {e.Exception.Message}\n\nThe app keeps running. Insertion has not been changed.",
                "CaptionBridge", MessageBoxButton.OK, MessageBoxImage.Warning);
            e.Handled = true;
        };

        var settings = SettingsStore.Load();
        var window = new MainWindow(settings);

        if (settings.StartMinimized && settings.MinimizeToTray)
        {
            new WindowInteropHelper(window).EnsureHandle(); // hotkeys work while hidden in the tray
        }
        else
        {
            if (settings.StartMinimized) window.WindowState = WindowState.Minimized;
            window.Show();
        }

        return app.Run();
    }
}
