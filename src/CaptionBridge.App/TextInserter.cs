using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Threading;
using System.Windows.Automation;
using CaptionBridge.Core;

namespace CaptionBridge;

internal enum InsertOutcome { Verified, Unverified, Failed, Aborted }

internal sealed record InsertResult(InsertOutcome Outcome, string Message, double Milliseconds);

/// <summary>Clipboard access lives on the UI (STA) thread; the worker calls it through this interface.</summary>
internal interface IClipboardAccess
{
    /// <summary>Copies the current clipboard contents so they can be put back later. Null means it was empty.</summary>
    object? Save();
    /// <summary>Puts text on the clipboard and returns the clipboard sequence number right after.</summary>
    uint SetText(string text);
    void Restore(object? saved);
}

/// <summary>
/// Inserts text at the caret of the focused field. Never selects, deletes or replaces existing text,
/// never presses Enter, never clicks anything.
///
/// A. Direct typing: each UTF-16 character is sent as a Unicode key event (SendInput + KEYEVENTF_UNICODE).
///    Works with accents, ñ, ¿ ¡ and emoji. Does not touch the clipboard.
/// B. Clipboard: saves the clipboard, sets the caption, presses Ctrl+V, then restores the old clipboard
///    only if no other app changed it in the meantime.
/// </summary>
internal sealed class TextInserter
{
    private const int ChunkChars = 24;
    private volatile bool _abort;

    public void Abort() => _abort = true;

    public InsertResult Insert(string rawText, FocusSnapshot target, AppSettings settings, IClipboardAccess clipboard)
    {
        _abort = false;
        var sw = Stopwatch.StartNew();
        string text = TextSanitizer.ForInsertion(rawText);
        if (text.Length == 0) return new InsertResult(InsertOutcome.Unverified, "Nothing to insert", 0);

        // Don't inject while the user is holding Ctrl/Alt/Shift/Win: the result would be a shortcut, not text.
        if (!WaitForModifiersReleased(TimeSpan.FromSeconds(1)))
            return new InsertResult(InsertOutcome.Failed, "Modifier keys were held down; caption not inserted", sw.Elapsed.TotalMilliseconds);

        if (NativeMethods.GetForegroundWindow() != target.ForegroundWindow)
            return new InsertResult(InsertOutcome.Aborted, "Focus moved to another window; caption not inserted", sw.Elapsed.TotalMilliseconds);

        string? before = settings.VerifyInsertion ? TryReadValue(target.Element) : null;

        string? error = settings.InsertionMethod == InsertionMethod.Clipboard
            ? PasteViaClipboard(text, target, settings, clipboard)
            : TypeDirectly(text, target);

        double ms = sw.Elapsed.TotalMilliseconds;
        if (error != null)
            return new InsertResult(_abort ? InsertOutcome.Aborted : InsertOutcome.Failed, error, ms);

        if (before == null) return new InsertResult(InsertOutcome.Unverified, "Inserted (field does not expose its text, not verified)", ms);

        // Give the target a moment to process the input, then check the text actually arrived.
        string probe = text.Trim();
        for (int i = 0; i < 4; i++)
        {
            Thread.Sleep(60);
            var after = TryReadValue(target.Element);
            if (after == null) break;
            if (after.Length > before.Length && after.Contains(probe, StringComparison.Ordinal))
                return new InsertResult(InsertOutcome.Verified, "Inserted and verified", ms);
        }
        return new InsertResult(InsertOutcome.Failed,
            "Sent, but the field's text did not change. This app may not accept simulated input. Try the other insertion method.", ms);
    }

    private string? TypeDirectly(string text, FocusSnapshot target)
    {
        var inputs = new List<NativeMethods.INPUT>(ChunkChars * 2);
        for (int start = 0; start < text.Length; start += ChunkChars)
        {
            if (_abort) return "Stopped";
            if (NativeMethods.GetForegroundWindow() != target.ForegroundWindow)
                return $"Focus moved away after {start} of {text.Length} characters; rest kept in preview";

            inputs.Clear();
            int end = Math.Min(text.Length, start + ChunkChars);
            // Don't split a surrogate pair (emoji) across chunks.
            if (end < text.Length && char.IsHighSurrogate(text[end - 1])) end++;
            for (int i = start; i < end; i++)
            {
                inputs.Add(NativeMethods.Unicode(text[i], false));
                inputs.Add(NativeMethods.Unicode(text[i], true));
            }
            var arr = inputs.ToArray();
            uint sent = NativeMethods.Send(arr);
            if (sent != arr.Length) return "Windows rejected the simulated input (the app may be elevated or protected)";
            start = end - ChunkChars; // keep loop step aligned when we extended for a surrogate pair
        }
        return null;
    }

    private static string? PasteViaClipboard(string text, FocusSnapshot target, AppSettings settings, IClipboardAccess clipboard)
    {
        object? saved;
        uint ourSequence;
        try
        {
            saved = clipboard.Save();
            ourSequence = clipboard.SetText(text);
        }
        catch (Exception ex)
        {
            return $"Clipboard is busy or unavailable ({ex.Message})";
        }

        if (NativeMethods.GetForegroundWindow() != target.ForegroundWindow)
        {
            TryRestore(clipboard, saved, ourSequence);
            return "Focus moved away before pasting";
        }

        var paste = new[]
        {
            NativeMethods.Key(NativeMethods.VK_CONTROL, false),
            NativeMethods.Key(NativeMethods.VK_V, false),
            NativeMethods.Key(NativeMethods.VK_V, true),
            NativeMethods.Key(NativeMethods.VK_CONTROL, true),
        };
        uint sent = NativeMethods.Send(paste);

        // The target reads the clipboard asynchronously after Ctrl+V. Wait before putting the old content back.
        Thread.Sleep(settings.ClipboardRestoreDelayMs);
        TryRestore(clipboard, saved, ourSequence);

        return sent == paste.Length ? null : "Windows rejected the simulated Ctrl+V";
    }

    private static void TryRestore(IClipboardAccess clipboard, object? saved, uint ourSequence)
    {
        try
        {
            if (ClipboardPolicy.ShouldRestore(ourSequence, NativeMethods.GetClipboardSequenceNumber()))
                clipboard.Restore(saved);
        }
        catch (Exception)
        {
            // Restoring is best effort; the caption stays on the clipboard.
        }
    }

    private static bool WaitForModifiersReleased(TimeSpan timeout)
    {
        var sw = Stopwatch.StartNew();
        while (NativeMethods.AnyModifierDown())
        {
            if (sw.Elapsed > timeout) return false;
            Thread.Sleep(30);
        }
        return true;
    }

    private static string? TryReadValue(AutomationElement? el)
    {
        if (el == null) return null;
        try
        {
            if (el.TryGetCurrentPattern(ValuePattern.Pattern, out var p))
            {
                var v = ((ValuePattern)p).Current.Value;
                return v != null && v.Length <= 200_000 ? v : null;
            }
        }
        catch (Exception ex) when (LiveCaptionsSource.IsUiaFailure(ex)) { }
        return null;
    }
}
