using CaptionBridge.Core;
using Xunit;

namespace CaptionBridge.Tests;

public class InsertionGateTests
{
    private static TargetInfo Notepad(string id = "notepad-1") => new()
    {
        Identity = id, ProcessId = 100, ProcessName = "Notepad", ControlType = "Document",
        ClassName = "RichEditD2DPT", HasTextPattern = true,
    };

    private static TargetInfo BrowserBox(string id = "chrome-1") => new()
    {
        Identity = id, ProcessId = 200, ProcessName = "chrome", ControlType = "Edit", IsReadOnly = false,
    };

    private static readonly TimeSpan T0 = TimeSpan.FromSeconds(1);

    private static InsertionGate ReadyGate(TargetInfo target)
    {
        var g = new InsertionGate();
        g.SetEnabled(true);
        g.ObserveFocus(target, T0);
        g.TakeSkipRequest();
        return g;
    }

    // ---------------------------------------------------------------- classification

    [Fact]
    public void Password_fields_are_rejected() =>
        Assert.False(TargetClassifier.IsInsertable(BrowserBox() with { IsPassword = true }, out _));

    [Fact]
    public void Read_only_fields_are_rejected() =>
        Assert.False(TargetClassifier.IsInsertable(BrowserBox() with { IsReadOnly = true }, out _));

    [Fact]
    public void Disabled_fields_are_rejected() =>
        Assert.False(TargetClassifier.IsInsertable(BrowserBox() with { IsEnabled = false }, out _));

    [Fact]
    public void Own_window_is_rejected() =>
        Assert.False(TargetClassifier.IsInsertable(BrowserBox() with { IsOwnProcess = true }, out _));

    [Fact]
    public void Elevated_apps_are_rejected() =>
        Assert.False(TargetClassifier.IsInsertable(BrowserBox() with { IsElevatedOrProtected = true }, out _));

    [Fact]
    public void Live_Captions_itself_is_rejected() =>
        Assert.False(TargetClassifier.IsInsertable(BrowserBox() with { ProcessName = "LiveCaptions" }, out _));

    [Fact]
    public void Buttons_and_other_controls_are_rejected() =>
        Assert.False(TargetClassifier.IsInsertable(BrowserBox() with { ControlType = "Button" }, out _));

    [Fact]
    public void Web_page_document_without_writable_value_is_not_verified()
    {
        var page = new TargetInfo { Identity = "page", ProcessName = "chrome", ControlType = "Document", IsReadOnly = true, HasTextPattern = true };
        Assert.False(TargetClassifier.IsInsertable(page, out _));
        Assert.False(TargetClassifier.IsInsertable(page with { IsReadOnly = null, ClassName = "Chrome_RenderWidgetHostHWND" }, out _));
    }

    [Fact]
    public void Edit_boxes_and_known_editors_are_accepted()
    {
        Assert.True(TargetClassifier.IsInsertable(BrowserBox(), out _));
        Assert.True(TargetClassifier.IsInsertable(Notepad(), out _));
        Assert.True(TargetClassifier.IsInsertable(new TargetInfo { Identity = "x", ControlType = "Document", IsReadOnly = false }, out _));
    }

    // ---------------------------------------------------------------- gate policy

    [Fact]
    public void Nothing_is_inserted_while_auto_insert_is_off()
    {
        var g = new InsertionGate();
        g.ObserveFocus(Notepad(), T0);
        g.Offer("hello", T0);
        Assert.Null(g.TakeInsertable(Notepad()));
        Assert.Equal(GateStatus.Disabled, g.Status);
    }

    [Fact]
    public void Enabling_requests_skipping_old_captions()
    {
        var g = new InsertionGate();
        g.TakeSkipRequest();
        g.SetEnabled(true);
        Assert.True(g.TakeSkipRequest());
        Assert.False(g.TakeSkipRequest());
    }

    [Fact]
    public void Text_goes_to_the_focused_valid_field()
    {
        var g = ReadyGate(Notepad());
        g.Offer("hello world", T0);
        Assert.Equal("hello world", g.TakeInsertable(Notepad()));
        Assert.Null(g.TakeInsertable(Notepad()));
    }

    [Fact]
    public void Switching_fields_drops_queued_text_and_requests_skip()
    {
        var g = ReadyGate(Notepad());
        g.Offer("meant for notepad", T0);
        g.ObserveFocus(BrowserBox(), T0);
        Assert.True(g.TakeSkipRequest());
        Assert.Null(g.TakeInsertable(BrowserBox()));
        Assert.Equal("", g.QueuedText);
    }

    [Fact]
    public void Focus_changing_between_offer_and_insert_is_caught()
    {
        var g = ReadyGate(Notepad());
        g.Offer("hello", T0);
        // the focused element re-checked right before insertion is a different field
        Assert.Null(g.TakeInsertable(BrowserBox()));
    }

    [Fact]
    public void Auto_pause_drops_captions_when_no_field_is_focused()
    {
        var g = ReadyGate(Notepad());
        g.ObserveFocus(null, T0);
        Assert.Equal(GateStatus.NoValidTarget, g.Status);
        g.Offer("lost", T0);
        g.ObserveFocus(Notepad(), T0);
        Assert.Null(g.TakeInsertable(Notepad()));
    }

    [Fact]
    public void Without_auto_pause_captions_wait_for_the_same_field_only()
    {
        var g = ReadyGate(Notepad());
        g.AutoPauseOnFocusLoss = false;
        g.ObserveFocus(null, T0);
        g.Offer("held", T0);
        g.ObserveFocus(Notepad(), T0 + TimeSpan.FromSeconds(2));
        Assert.Equal("held", g.TakeInsertable(Notepad()));

        g.ObserveFocus(null, T0);
        g.Offer("not for chrome", T0);
        g.ObserveFocus(BrowserBox(), T0);
        Assert.Null(g.TakeInsertable(BrowserBox()));
    }

    [Fact]
    public void Held_captions_expire()
    {
        var g = ReadyGate(Notepad());
        g.AutoPauseOnFocusLoss = false;
        g.ObserveFocus(null, T0);
        g.Offer("too old", T0);
        g.ObserveFocus(Notepad(), T0 + InsertionGate.MaxQueueAge + TimeSpan.FromSeconds(1));
        Assert.Null(g.TakeInsertable(Notepad()));
    }

    [Fact]
    public void Lock_mode_only_inserts_into_the_locked_field()
    {
        var g = new InsertionGate();
        g.SetEnabled(true);
        g.ArmLock();
        g.ObserveFocus(null, T0);                 // the app itself is focused when Lock is clicked
        g.ObserveFocus(Notepad(), T0);            // next valid field gets locked
        Assert.Equal("notepad-1", g.LockedTargetId);

        g.ObserveFocus(BrowserBox(), T0);
        Assert.Equal(GateStatus.WaitingForLockedTarget, g.Status);
        g.Offer("x", T0);
        Assert.Null(g.TakeInsertable(BrowserBox()));

        g.ObserveFocus(Notepad(), T0);
        Assert.Equal(GateStatus.Ready, g.Status);
        g.Offer("y", T0);
        Assert.Equal("y", g.TakeInsertable(Notepad()));
    }

    [Fact]
    public void Emergency_stop_disables_and_clears_everything()
    {
        var g = ReadyGate(Notepad());
        g.Offer("pending text", T0);
        g.EmergencyStop();
        Assert.False(g.Enabled);
        Assert.Equal("", g.QueuedText);
        g.ObserveFocus(Notepad(), T0);
        g.Offer("after stop", T0);
        Assert.Null(g.TakeInsertable(Notepad()));
    }

    [Fact]
    public void Pause_and_resume_skips_what_was_said_while_paused()
    {
        var g = ReadyGate(Notepad());
        g.SetPaused(true);
        g.ObserveFocus(Notepad(), T0);
        g.Offer("while paused", T0);
        g.SetPaused(false);
        Assert.True(g.TakeSkipRequest());
        g.ObserveFocus(Notepad(), T0);
        Assert.Null(g.TakeInsertable(Notepad()));
    }

    [Fact]
    public void Queue_is_bounded()
    {
        var g = ReadyGate(Notepad());
        for (int i = 0; i < 1000; i++) g.Offer("0123456789", T0);
        Assert.True(g.QueuedText.Length <= InsertionGate.MaxQueuedChars);
    }

    // ---------------------------------------------------------------- clipboard and text

    [Fact]
    public void Clipboard_is_restored_only_if_nobody_else_changed_it()
    {
        Assert.True(ClipboardPolicy.ShouldRestore(41, 41));
        Assert.False(ClipboardPolicy.ShouldRestore(41, 42)); // another app copied something meanwhile
    }

    [Theory]
    [InlineData("hola\nmundo", "hola mundo")]
    [InlineData("tab\there", "tab here")]
    [InlineData("¿Qué tal? ¡Año ñandú!", "¿Qué tal? ¡Año ñandú!")]
    [InlineData("bell\u0007", "bell")]
    [InlineData("emoji 😀 ok", "emoji 😀 ok")]
    public void Text_is_sanitized_without_touching_unicode(string input, string expected) =>
        Assert.Equal(expected, TextSanitizer.ForInsertion(input));

    // ---------------------------------------------------------------- settings

    [Fact]
    public void Default_hotkeys_are_F8_start_and_F9_stop()
    {
        var s = new AppSettings();
        Assert.Equal("F8", s.HotkeyStart);
        Assert.Equal("F9", s.HotkeyStop);
    }

    [Fact]
    public void Old_settings_files_move_to_F8_F9()
    {
        var old = AppSettings.FromJson("{ \"HotkeyStop\": \"Ctrl+Alt+Shift+S\", \"StabilizationDelayMs\": 600 }");
        Assert.Equal("F8", old.HotkeyStart);
        Assert.Equal("F9", old.HotkeyStop);
        Assert.Equal(600, old.StabilizationDelayMs);

        var custom = AppSettings.FromJson("{ \"HotkeyStart\": \"F6\", \"HotkeyStop\": \"F7\" }");
        Assert.Equal("F6", custom.HotkeyStart);
        Assert.Equal("F7", custom.HotkeyStop);
    }

    [Fact]
    public void Settings_round_trip_and_clamp()
    {
        var s = new AppSettings { PollIntervalMs = 5, InsertionMethod = InsertionMethod.Clipboard };
        var back = AppSettings.FromJson(s.ToJson());
        Assert.Equal(InsertionMethod.Clipboard, back.InsertionMethod);
        Assert.Equal(50, back.PollIntervalMs);
        Assert.Equal(500, AppSettings.FromJson("{ not json").StabilizationDelayMs);
    }

    [Theory]
    [InlineData("F8", true)]
    [InlineData("F9", true)]
    [InlineData("Ctrl+F9", true)]
    [InlineData("Ctrl+Alt+Shift+I", true)]
    [InlineData("Alt+F4", false)]    // closes windows
    [InlineData("Ctrl+C", false)]    // copy
    [InlineData("Shift+A", false)]   // would fire while typing
    [InlineData("A", false)]
    [InlineData("Win+L", false)]     // reserved by Windows
    [InlineData("Ctrl+Alt", false)]
    [InlineData("", false)]
    public void Hotkey_parsing(string text, bool ok) =>
        Assert.Equal(ok, HotkeyParser.TryParse(text, out _, out _));
}

public class AppSettingsTests
{
    [Fact]
    public void ShowTranscript_DefaultsOff_AndRoundTrips()
    {
        Assert.False(new CaptionBridge.Core.AppSettings().ShowTranscript);
        var s = new CaptionBridge.Core.AppSettings { ShowTranscript = true };
        Assert.True(CaptionBridge.Core.AppSettings.FromJson(s.ToJson()).ShowTranscript);
    }
}

public class HotkeyNormalizeTests
{
    [Theory]
    [InlineData("f8", "F8")]
    [InlineData("shift + alt + ctrl + i", "Ctrl+Alt+Shift+I")]
    [InlineData("Ctrl+F9", "Ctrl+F9")]
    [InlineData("Ctrl+C", null)]
    [InlineData("Alt+F4", null)]
    [InlineData("", null)]
    public void Normalize_GivesCanonicalTextOrNull(string text, string? expected) =>
        Assert.Equal(expected, HotkeyParser.Normalize(text));

    [Theory]
    [InlineData("F8", "f8", true)]
    [InlineData("Alt+Ctrl+I", "Ctrl+Alt+I", true)]
    [InlineData("F8", "F9", false)]
    [InlineData("", "", false)]
    public void SameKey_ComparesCombinations(string a, string b, bool same) =>
        Assert.Equal(same, HotkeyParser.SameKey(a, b));
}
