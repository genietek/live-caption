# CaptionBridge for iPad

A web app, published by **Genie**, that shows live captions of what the iPad's microphone hears.
It runs in Safari, so it needs no Mac, no App Store and no Apple Developer account.

Address: **https://genietek.github.io/live-caption/**

## Install on the iPad

1. Open the address above in **Safari** (iPadOS 14.5 or later).
2. Tap **Start**, then tap **Allow** when Safari asks for the microphone.
3. To get an app icon, tap the Share button, then **Add to Home Screen**.
   If iPadOS shows an **Open as Web App** switch, leave it **off**. Safari's speech recognition is
   unreliable inside home-screen web apps, so the icon opens the page in Safari instead.

If Start shows a "blocked" message, open the iPad's Settings > Apps > Safari > Microphone and choose
Ask or Allow, and check that Settings > Privacy & Security > Speech Recognition allows Safari.

## What it does

- Live captions in large text, with the line still being spoken shown at full brightness.
- Settings for language, text size, number of lines on screen, colours, and keeping the screen on.
- A collapsible **Transcript (editable)** with one line per caption, kept on the iPad between visits.
- **Copy line** copies the latest caption, and **Copy all**, **Share** and **Save .txt** send the
  transcript to other apps. Use these to paste captions into Notes, Mail, or a chat.

## Limits

- It captions the microphone only. iPadOS does not let web apps hear sound from other apps.
- It cannot type into other apps by itself the way CaptionBridge does on Windows. iPadOS blocks that.
- Captioning pauses while Safari is in the background or the screen is locked, and resumes when you come back.
- Speech is recognised by Apple's service in Safari. Depending on the language and iPad, audio may be sent
  to Apple for recognition. The transcript itself is stored only on the iPad.
- Not yet tested on a real iPad. The automated tests use a stand-in for Safari's speech engine.

## Development

Plain HTML, CSS and JavaScript with no build step and no dependencies.

- `captions.js` is the caption logic. Unit tests: `node --test ipad/tests/captions.test.mjs`
- `tests/browser.test.cjs` drives the page in Chromium with a fake speech engine. Serve the folder with
  `cd ipad && python3 -m http.server 8765`, then run `node ipad/tests/browser.test.cjs`.
- `.github/workflows/ipad-pages.yml` runs both tests and publishes the folder to GitHub Pages on every push to main.
  One-time setup: in the repository's Settings > Pages, set Source to **GitHub Actions**.
