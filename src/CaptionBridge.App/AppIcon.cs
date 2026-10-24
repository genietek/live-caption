using System.IO;
using System.Windows.Media;
using System.Windows.Media.Imaging;

namespace CaptionBridge;

/// <summary>Loads the Genie CaptionBridge logo embedded in the exe.</summary>
internal static class AppIcon
{
    private const string ResourceName = "CaptionBridge.app.ico";

    private static Stream Open() =>
        typeof(AppIcon).Assembly.GetManifestResourceStream(ResourceName)
        ?? throw new FileNotFoundException("Embedded icon missing", ResourceName);

    /// <summary>Window and taskbar icon (WPF picks the best size from the .ico).</summary>
    public static ImageSource ForWindow()
    {
        using var s = Open();
        return BitmapFrame.Create(s, BitmapCreateOptions.None, BitmapCacheOption.OnLoad);
    }

    /// <summary>System tray icon at the small size Windows uses for the notification area.</summary>
    public static System.Drawing.Icon ForTray()
    {
        using var s = Open();
        return new System.Drawing.Icon(s, System.Windows.Forms.SystemInformation.SmallIconSize);
    }
}
