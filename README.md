# CaptionBridge

CaptionBridge is a small Windows 11 app. It takes the text that **Windows Live Captions** shows and types
the new words into whatever textbox you have focused, such as Notepad, a browser field, or a chat box.

It does no speech recognition itself. Windows Live Captions does that on your PC. CaptionBridge only reads
the caption text through Windows UI Automation (the accessibility API). It records no audio, uses no
cloud service, uses no GPU, and sends nothing over the network.

> **Status:** version 0.1.0. It builds and its unit tests pass, but it has **not yet been tested
> end to end on a real Windows 11 PC with Live Captions**. Compatibility with specific apps is unverified
> until it is tested (see "Manual end-to-end test" below).

## Quick start

1. Unzip `CaptionBridge-win-x64.zip` anywhere, for example into `Documents\CaptionBridge`.
2. Double-click `CaptionBridge.exe`. It needs no installation and no admin rights.
   Windows SmartScreen may warn because the exe is not code-signed. Click "More info", then "Run anyway".
3. Turn on Live Captions with **Win + Ctrl + L**, or click **Launch Live Captions**.
   The first time, Windows asks to download the speech files.
4. Check that the app shows **Live Captions: Connected to Live Captions** with a green dot.
5. Click **Enable Auto Insert**.
6. Click into a textbox in another app and start talking, or play a video.
   New words appear in the textbox after a short delay (0.7 s by default).
7. To stop at once, click **STOP INSERTION** or press **Ctrl+Alt+Shift+S**.

## What it does and does not do

- It inserts at the text cursor. It never selects, replaces or deletes existing text.
- It never presses Enter, never clicks Send, and never submits a form. Newlines in captions become spaces.
- It does not type into password fields, read-only or disabled fields, buttons, plain web pages,
  apps running as administrator, Windows security prompts, Live Captions itself, or its own window.
- It only inserts while **Auto Insert** is on. Auto Insert starts off unless you change that in Settings.
- When you move to a different textbox, captions spoken before the switch are **not** sent to the new box.
- It pauses automatically when no valid textbox is focused, when Windows is locked, and when the PC sleeps.
- It never logs keystrokes. Global hotkeys use `RegisterHotKey`, which only sees the three combos you set.

## Main window

| Area | Meaning |
| --- | --- |
| STOP INSERTION | Turns Auto Insert off at once, even mid-sentence, and throws away queued captions. |
| Live Captions | Connection status. Green means connected, yellow means waiting, grey means capture is stopped. |
| Auto Insert | ON, ON (paused) or OFF. |
| Focused / Target status | The focused app and control, and whether text can go there (and why not). |
| Target lock | The optional lock to a single textbox. |
| Last insertion | The result of the last insertion: verified, unverified (the app does not expose its text), or NOT inserted with the reason. |
| Listening | Words Live Captions is still revising. These are not inserted yet. |
| Captions | Committed text. Everything that was or would have been inserted. Select it to copy. |
| Bottom line | Live measured CPU, RAM, read and insert timings, and counters. |

Buttons: **Start/Stop capture**, **Enable/Disable Auto Insert**, **Pause/Resume**,
**Lock to next textbox / Unlock target**, and **Launch Live Captions** (sends Win+Ctrl+L).

**Lock mode:** click *Lock to next textbox*, then click into the textbox you want. From then on, text goes
only into that textbox. While it is not focused, captions are not inserted anywhere.

## Settings

Settings are saved to `%APPDATA%\CaptionBridge\settings.json`.

| Setting | Default | Notes |
| --- | --- | --- |
| Enable Auto Insert when the app starts | off | |
| Start capturing captions when the app starts | on | |
| Stabilization delay | 700 ms | How long a word must stay unchanged before it is inserted. A higher value means fewer wrong words but more lag. |
| Caption polling interval | 200 ms | Slows down to 600 ms or less when captions have been idle for 10 s. |
| Insert | Stable phrases | *Complete sentences only* waits for `. ? ! …` and fixes fewer mistakes mid-sentence. |
| Silence flush | 1500 ms | In sentence mode, an unfinished sentence is inserted after this much silence. |
| Insertion method | Direct typing | Types Unicode key events. *Clipboard paste* uses Ctrl+V and restores your clipboard afterwards. |
| Clipboard restore delay | 250 ms | How long to wait after Ctrl+V before putting your old clipboard back. |
| Pause automatically when no valid textbox is focused | on | When off, captions are held (up to 10 s, 2000 characters) and inserted only if the **same** textbox is focused again. |
| Verify insertion | on | Re-reads the field afterwards when the app allows it, and reports a failure if the text did not arrive. |
| Minimize to the system tray, Start minimized | on, off | When minimized, the app lives in the tray. Right-click the tray icon for Show, Auto Insert, STOP and Exit. |
| Global hotkeys | on | Toggle Auto Insert `Ctrl+Alt+Shift+I`, pause/resume `Ctrl+Alt+Shift+P`, stop `Ctrl+Alt+Shift+S`. Each hotkey must use two of Ctrl/Alt/Shift plus a letter, digit or F-key. Win-key combos are refused so they cannot clash with Windows shortcuts. |

## English and Spanish

CaptionBridge inserts exactly the text Live Captions produces, including accents, ñ/Ñ, ¿ ¡ and other Unicode.
It does not detect, translate or change the language.

You choose the caption language inside Live Captions (gear icon, then Language). Which languages are
available depends on your Windows version, installed language packs and device. If Spanish is not
offered there, CaptionBridge cannot enable it.

## How caption extraction works

Live Captions shows a few lines of text that keep changing. The last words are revised as you speak,
punctuation and capitals are fixed afterwards, and old lines scroll off the top. So CaptionBridge never
pastes the whole caption. Instead, `CaptionProcessor` does the following:

1. Splits each snapshot into words. For matching, it compares a lowercase, punctuation-free,
   Unicode-normalised form of each word, so "hello how" still matches "Hello, how".
2. Finds where the already-inserted words are in the new snapshot. It aligns the last 10 committed words
   using a small edit-distance match. This tolerates rewritten words, split or merged words, and lines
   scrolling off the top. When a phrase is repeated, it uses the position the previous snapshot predicts.
3. Treats every word after that point as *pending*, and timestamps each word when it first appears in its
   current form.
4. Commits pending words once they have stayed unchanged for the stabilization delay. In sentence mode,
   it also waits for the end of a sentence or for a silence.

Memory stays bounded. It keeps at most 64 committed words, the current snapshot (capped at 400 words),
6000 characters of preview, and a queue of 2000 characters.

### Correction policy (and its trade-offs)

- **Before a word is inserted:** revisions are free. Only the final stable form is inserted.
  This is the reason for the stabilization delay.
- **After a word is inserted:** if Live Captions later rewrites it (for example "want" becomes "wanted"),
  CaptionBridge **does not go back and change it**. Doing so would mean selecting or deleting text in
  your textbox. You may have typed or moved the cursor in the meantime, so deleting could destroy your
  own text. The rewrite is counted under "corrections ignored" and skipped, and the new words after it
  still go in.
- **One exception:** if Live Captions only adds punctuation to the last inserted word ("home" becomes
  "home."), that punctuation is appended.
- **Trade-off:** occasionally a word Live Captions later corrected stays wrong in your textbox. A longer
  stabilization delay or sentence mode makes this rarer, at the cost of more lag.
- **Resets:** if the caption text no longer contains any recent committed words (Live Captions was
  restarted or cleared), the visible text is treated as new speech. This is counted under "resets".

## Text insertion

- **Direct typing** (default) sends each character as a Unicode key event with `SendInput`. It works with
  any language and does not touch the clipboard. Some apps (some games, remote desktops, terminals in
  special modes) ignore simulated keys.
- **Clipboard paste** works like this:
  1. Checks that the target is still focused.
  2. Saves the clipboard (every format that can be read).
  3. Puts the caption on the clipboard.
  4. Presses Ctrl+V.
  5. Waits for the restore delay.
  6. Restores the old clipboard **only if no other app changed it in the meantime** (it checks the
     Windows clipboard sequence number).
- Before every insertion, it re-checks that the same window and control are still focused, and waits for
  Ctrl, Alt, Shift and Win to be released.
- If an insertion fails, the reason is shown and the text stays in the Captions preview. It is never
  retried automatically, so it can never be inserted twice.

## Build

You need the [.NET 8 SDK](https://dotnet.microsoft.com/download/dotnet/8.0). No other dependencies are
needed; there are no NuGet packages in the app (xUnit is used for tests only).

```powershell
dotnet build CaptionBridge.sln -c Release
dotnet test tests\CaptionBridge.Tests -c Release
# a single self-contained exe, no .NET install needed on the target PC (~66 MB)
dotnet publish src\CaptionBridge.App -c Release -r win-x64 --self-contained true -p:PublishSingleFile=true -p:IncludeNativeLibrariesForSelfExtract=true -p:EnableCompressionInSingleFile=true -o publish
# or a small exe that needs the .NET 8 Desktop Runtime installed
dotnet publish src\CaptionBridge.App -c Release -r win-x64 --self-contained false -p:PublishSingleFile=true -o publish-small
```

The UI is written in C# without XAML, so the projects also build on Linux and macOS. The app only runs
on Windows. CI (`.github/workflows/ci.yml`) builds, tests, publishes, and starts the app on a Windows
runner on every push.

Project layout:

```
src/CaptionBridge.Core     platform-neutral: CaptionProcessor, InsertionGate (focus policy), settings
src/CaptionBridge.App      Windows: UI Automation reader, focus probe, text inserter, hotkeys, WPF window, tray
tests/CaptionBridge.Tests  unit tests with mock caption streams and mock targets
diagnostics/LiveCaptionsProbe  Phase 1 console probe (prints the Live Captions UIA tree and timings)
```

## Requirements and compatibility

- Windows 11 (22H2 or later) with Live Captions, x64.
  Windows 10 has no Live Captions, so the app will just report that it is not running.
- No admin rights. The app runs as a normal user (`asInvoker`) and does not bypass Windows input protection.
  Because of that, Windows does not let it type into apps that run as administrator.
- No GPU, CUDA, AI model, or internet needed.
- It finds Live Captions by its UI Automation class `LiveCaptionsDesktopWindow` and element id
  `CaptionsTextBlock`. A future Windows update could change these names. If it does, run
  `diagnostics/LiveCaptionsProbe` and report its output.

**Tested apps:** none yet on real hardware. Expected to work, but to be confirmed:

| Target | Notes |
| --- | --- |
| Notepad (Windows 11) | Known editor class, accepted. |
| Chrome / Edge / Firefox `<input>` and `<textarea>` | Exposed as Edit controls. |
| Contenteditable editors, Teams chat (WebView2) | Accepted only when the browser reports the element as an editable Edit, or as a Document that is not read-only. Otherwise the app shows "not verified as editable" and skips it. |
| Microsoft Word | Known editor class, accepted. |

## Troubleshooting

| Symptom | What to do |
| --- | --- |
| "Live Captions is not running" | Press Win+Ctrl+L. Live Captions needs Windows 11 22H2 or later. |
| "window found, but the caption element was not" | Live Captions may still be starting; wait a few seconds. If it stays like that, run `LiveCaptionsProbe.exe --tree` and share its output. |
| Target status says "Not a text field" or "not verified as editable" | That control does not report itself as editable through accessibility. Try clicking directly inside the text area. |
| "Target runs as administrator or is protected" | Windows blocks normal apps from typing into elevated apps. Run that app without admin rights. Running CaptionBridge as admin also works, but is not recommended. |
| Last insertion says "the field's text did not change" | The app ignored simulated input. Switch Insertion method (Direct typing or Clipboard paste) in Settings. |
| Words are glued together or a space is missing at the start | The first insertion into a new textbox has no leading space. Type a space before you start. |
| Hotkey "already used by another app" | Choose a different combo in Settings and click Save. |
| Wrong words stay in the textbox | That is the correction policy. Raise the stabilization delay, or use "Complete sentences only". |
| SmartScreen blocks the exe | The exe is unsigned. Click "More info", then "Run anyway", or build it yourself from source. |

## Manual end-to-end test (Windows 11)

1. Open Notepad and click into it.
2. Start CaptionBridge, then Live Captions (Win+Ctrl+L). Wait for the green dot.
3. Enable Auto Insert, click back into Notepad, and play an English video for 2 minutes.
   Check that no words are duplicated or missing compared with the Captions preview.
4. Type a few characters yourself in the middle. Check they are not overwritten.
5. Click into a second Notepad window. Captions continue there, without the earlier sentence.
6. Focus a browser page with no text field. The status says it is paused, and nothing is typed.
7. Press Ctrl+Alt+Shift+S while speech is running. Typing stops immediately.
8. Close Live Captions and reopen it. The app reconnects on its own.
9. Lock Windows (Win+L) and unlock it. Insertion resumes without the old text.
10. Switch Live Captions to Spanish and say "¿Cómo estás? Mañana, ¡qué bien!". The accents and ¿ ¡ arrive intact.
11. Repeat steps 3 to 7 in Chrome or Edge (a textarea), in Teams chat, and with Clipboard paste mode.
    Copy something before the test and check it is back on the clipboard afterwards.
12. Leave it running for an hour. The CPU and RAM numbers on the bottom line should stay flat.
