using System.IO;
using System.Windows.Media;
using System.Windows.Media.Imaging;

namespace CaptionBridge;

/// <summary>Loads the Genie CaptionBridge logo embedded in the exe.</summary>
internal static class AppIcon
{
    private const string OnResource = "CaptionBridge.app.ico";
    private const string OffResource = "CaptionBridge.app-off.ico";

    private static Stream Open(string name = OnResource) =>
        typeof(AppIcon).Assembly.GetManifestResourceStream(name)
        ?? throw new FileNotFoundException("Embedded icon missing", name);

    /// <summary>Window and taskbar icon (WPF picks the best size from the .ico).</summary>
    public static ImageSource ForWindow()
    {
        using var s = Open();
        return BitmapFrame.Create(s, BitmapCreateOptions.None, BitmapCacheOption.OnLoad);
    }

    /// <summary>
    /// System tray icon at the small size Windows uses for the notification area:
    /// the original logo while Auto Insert is on, the logo on a gray background while it is off.
    /// </summary>
    public static System.Drawing.Icon ForTray(bool autoInsertOn)
    {
        using var s = Open(autoInsertOn ? OnResource : OffResource);
        return new System.Drawing.Icon(s, System.Windows.Forms.SystemInformation.SmallIconSize);
    }
}
