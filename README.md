# CaptionBridge

CaptionBridge is a small Windows 11 app. It takes the text that **Windows Live Captions** shows and types
the new words into whatever textbox you have focused, such as Notepad, a browser field, or a chat box.

It does no speech recognition itself. Windows Live Captions does that on your PC. CaptionBridge only reads
the caption text through Windows UI Automation (the accessibility API). It records no audio, uses no
cloud service, uses no GPU, and sends nothing over the network.

> **Status:** version 0.4.5, published by Genie. It builds, its unit tests pass, and Windows CI starts the app on every
> change. End-to-end behaviour with Live Captions on a real PC is still being confirmed, and compatibility
> with specific apps is unverified until it is tested (see "Manual end-to-end test" below).

## Publisher

CaptionBridge is published by **Genie**. The exe carries "Genie" as its company and copyright in
File Properties > Details, plus the CaptionBridge logo. The exe is not code-signed, so Windows SmartScreen
and security prompts still show "Unknown publisher". Showing "Genie" there needs a code-signing
certificate issued to Genie. Every CI build checks that the published exe still says Genie, and fails if it does not.

## Quick start

1. Unzip the zip anywhere, for example into `Documents\CaptionBridge`.
2. Double-click `CaptionBridge.exe`. It needs no installation and no admin rights.
   Windows SmartScreen may warn because the exe is not code-signed. Click "More info", then "Run anyway".
3. Turn on Live Captions with **Win + Ctrl + L**, or click **Open Live Captions**. If you skip this, starting Auto Insert opens it for you.
   The first time, Windows asks to download the speech files.
4. Wait for the green dot and "Live Captions connected".
5. Press **F8**, or click **Start Auto Insert**.
6. Click into a textbox in another app and start talking, or play a video.
   New words appear in the textbox about half a second after Live Captions settles on them.
7. Press **F9**, or click **Stop Auto Insert**, to stop at once.

F8 and F9 work from any app, even when CaptionBridge is minimized. While CaptionBridge runs, those two
keys are reserved for it. If another app needs them, choose other keys in Settings.

## What it does and does not do

- It inserts at the text cursor. It never selects, replaces or deletes existing text.
- It never presses Enter, never clicks Send, and never submits a form. Newlines in captions become spaces.
- It does not type into password fields, read-only or disabled fields, buttons, plain web pages,
  apps running as administrator, Windows security prompts, Live Captions itself, or its own window.
- It only inserts after you press F8 or click Start. Auto Insert starts off unless you change that in Settings.
- When you move to a different textbox, captions spoken before the switch are **not** sent to the new box.
- It pauses automatically when no valid textbox is focused, when Windows is locked, and when the PC sleeps.
- It never logs keystrokes. Global hotkeys use `RegisterHotKey`, which only sees the two keys you set.

## Main window

| Area | Meaning |
| --- | --- |
| Dot and status line | Green means Live Captions is connected. Yellow means the app is waiting for it and says why. |
| Start / Stop Auto Insert | The green button starts (F8). The red button stops immediately (F9), even mid-sentence, and discards queued text. |
| Line under the button | "Typing into Notepad (…)" when text can go into the focused field, otherwise why not (for example, "Not a text field"). |
| Transcript (editable) | Click to show or hide; the app remembers your choice (hidden by default). It holds everything committed, whether it was typed or not. You can edit or copy the text. New captions are added at the end, and your edits are kept. |
| Gray "…" line | Words Live Captions is still revising. These are not typed yet. |
| Message line | The latest event. It turns red when an insertion failed, and shows why. |
| Open Live Captions | Opens Windows Live Captions if it is not running (it never closes it). Starting Auto Insert does the same automatically. |
| Copy diagnostics | Copies the app's state for a bug report. It includes the last caption line. |

The tray icon shows the logo in color while Auto Insert is on, and on a gray background while it is off.
Right-click it for Show, Start, Stop and Exit.

## Settings

Click **Settings** at the bottom of the window. Settings are saved to `%APPDATA%\CaptionBridge\settings.json`.

| Setting | Default | Notes |
| --- | --- | --- |
| Start hotkey / Stop hotkey | F8 / F9 | Click the button, then press the new key; it is saved right away. Any F-key on its own (F1 to F24), or with modifiers. A letter or digit needs two of Ctrl/Alt/Shift. Win combos and Alt+F4 are refused. Esc cancels, Backspace turns that hotkey off. Set both to the same key to turn Auto Insert on and off with one key. |
| Wait before typing a word | 500 ms | How long a word must stay unchanged before it is typed. Higher means fewer wrong words but more lag. |
| Type | Words as soon as stable | *Whole sentences* waits for `. ? ! …`, or for 1.5 s of silence. |
| Method | Typing | Types Unicode key events. *Clipboard paste* uses Ctrl+V and puts your old clipboard back afterwards. |
| When I click X | Ask every time | Clicking the window's X asks whether to keep CaptionBridge running in the tray or close it, with a "Remember my choice" box. You can also pick *Keep running in tray* or *Close the app* here. In the tray, right-click the icon and choose Exit to close it. |
| Minimize to tray, Start minimized | on, off | |
| Start Auto Insert when the app opens | off | |

Advanced values can be edited in `settings.json` while the app is closed. They are:
`PollIntervalMs` (200), `SilenceFlushMs` (1500), `ClipboardRestoreDelayMs` (250),
`AutoPauseOnFocusLoss` (true) and `VerifyInsertion` (true).
When `AutoPauseOnFocusLoss` is false, captions are held for up to 10 s and 2000 characters, and are
inserted only if the **same** textbox gets focus again.

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
6000 characters of transcript in the engine (20000 in the window), and a queue of 2000 characters.

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
- If an insertion fails, the reason is shown and the text stays in the Transcript section. It is never
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
| "Live Captions is not running" | Start Auto Insert or click Open Live Captions, or press Win+Ctrl+L. Live Captions needs Windows 11 22H2 or later. The first time, Live Captions may ask you to download a language pack. |
| "window found, but the caption element was not" | Live Captions may still be starting; wait a few seconds. If it stays like that, run `LiveCaptionsProbe.exe --tree` and share its output. |
| The line under the button says "Not a text field" or "not verified as editable" | That control does not report itself as editable through accessibility. Try clicking directly inside the text area. |
| "Target runs as administrator or is protected" | Windows blocks normal apps from typing into elevated apps. Run that app without admin rights. Running CaptionBridge as admin also works, but is not recommended. |
| The message line says "the field's text did not change" | The app ignored simulated input. Switch Method (Typing or Clipboard paste) in Settings. |
| Words are glued together or a space is missing at the start | The first insertion into a new textbox has no leading space. Type a space before you start. |
| Hotkey "already used by another app", or F8/F9 do nothing | Another app has taken that key. In Settings, click the hotkey and press a different key. |
| Wrong words stay in the textbox | That is the correction policy. Raise "Wait before typing a word", or switch Type to "Whole sentences". |
| SmartScreen blocks the exe | The exe is unsigned. Click "More info", then "Run anyway", or build it yourself from source. |

## Manual end-to-end test (Windows 11)

1. Open Notepad and click into it.
2. Start CaptionBridge, then Live Captions (Win+Ctrl+L). Wait for the green dot.
3. Press F8, click back into Notepad, and play an English video for 2 minutes.
   Check that no words are duplicated or missing compared with the Transcript section.
4. Type a few characters yourself in the middle. Check they are not overwritten.
5. Click into a second Notepad window. Captions continue there, without the earlier sentence.
6. Focus a browser page with no text field. The status says it is paused, and nothing is typed.
7. Press F9 while speech is running. Typing stops immediately.
8. Close Live Captions and reopen it. The app reconnects on its own.
9. Lock Windows (Win+L) and unlock it. Insertion resumes without the old text.
10. Switch Live Captions to Spanish and say "¿Cómo estás? Mañana, ¡qué bien!". The accents and ¿ ¡ arrive intact.
11. Repeat steps 3 to 7 in Chrome or Edge (a textarea), in Teams chat, and with Clipboard paste mode.
    Copy something before the test and check it is back on the clipboard afterwards.
12. Leave it running for an hour. The CPU and RAM numbers on the bottom line should stay flat.
