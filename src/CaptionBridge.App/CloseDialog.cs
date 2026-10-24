using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using CaptionBridge.Core;

namespace CaptionBridge;

/// <summary>Asked when the window's X is clicked: keep running in the tray, or close the app.</summary>
internal sealed class CloseDialog : Window
{
    private readonly CheckBox _remember = new()
    {
        Content = "Remember my choice (you can change it in Settings)", Margin = new Thickness(0, 12, 0, 0),
    };

    public CloseAction Choice { get; private set; } = CloseAction.Exit;
    public bool Remember => _remember.IsChecked == true;

    public CloseDialog()
    {
        Title = "Close CaptionBridge";
        Width = 360;
        SizeToContent = SizeToContent.Height;
        ResizeMode = ResizeMode.NoResize;
        WindowStartupLocation = WindowStartupLocation.CenterOwner;
        ShowInTaskbar = false;
        Background = SystemColors.WindowBrush;
        FontFamily = new FontFamily("Segoe UI");

        var panel = new StackPanel { Margin = new Thickness(16) };
        panel.Children.Add(new TextBlock
        {
            Text = "Keep CaptionBridge running in the tray, or close it?", FontSize = 14, TextWrapping = TextWrapping.Wrap,
        });
        panel.Children.Add(new TextBlock
        {
            Text = "In the tray, F8/F9 keep working. To close it later, right-click the tray icon and choose Exit.",
            FontSize = 12, Foreground = Brushes.DimGray, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 6, 0, 0),
        });
        panel.Children.Add(_remember);

        var buttons = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right, Margin = new Thickness(0, 14, 0, 0) };
        buttons.Children.Add(MakeButton("Keep in tray", CloseAction.KeepInTray, isDefault: true));
        buttons.Children.Add(MakeButton("Close app", CloseAction.Exit, isDefault: false));
        panel.Children.Add(buttons);
        Content = panel;
    }

    private Button MakeButton(string text, CloseAction action, bool isDefault)
    {
        var b = new Button { Content = text, IsDefault = isDefault, Padding = new Thickness(14, 4, 14, 4), Margin = new Thickness(8, 0, 0, 0) };
        b.Click += (_, _) =>
        {
            Choice = action;
            DialogResult = true;
        };
        return b;
    }
}
