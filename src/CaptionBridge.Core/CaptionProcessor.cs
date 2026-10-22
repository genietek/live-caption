using System.Text;

namespace CaptionBridge.Core;

public enum CommitMode
{
    /// <summary>Commit each word once it has stopped changing for the stabilization delay.</summary>
    StablePhrases,

    /// <summary>Commit only up to the last finished sentence, or everything after a silence.</summary>
    CompleteSentences,
}

public sealed class CaptionProcessorOptions
{
    /// <summary>How long a word must stay unchanged before it is committed.</summary>
    public TimeSpan StabilizationDelay { get; set; } = TimeSpan.FromMilliseconds(700);

    public CommitMode Mode { get; set; } = CommitMode.StablePhrases;

    /// <summary>In sentence mode, commit an unfinished sentence after the captions stop changing this long.</summary>
    public TimeSpan SilenceFlush { get; set; } = TimeSpan.FromMilliseconds(1500);

    /// <summary>How many committed words are remembered for alignment. Bounds memory.</summary>
    public int HistoryWords { get; set; } = 64;

    /// <summary>Only the last N words of a snapshot are examined. Bounds CPU.</summary>
    public int MaxSnapshotWords { get; set; } = 400;

    /// <summary>How many of the most recent committed words are used to find our place in a new snapshot.</summary>
    public int AlignWindow { get; set; } = 10;
}

/// <summary>
/// Turns successive full snapshots of the Live Captions text into a stream of newly committed text.
///
/// Live Captions shows a few lines that keep changing: the last words are revised while you speak,
/// punctuation and capitalisation get fixed up, and old lines scroll off the top. This class:
///  - finds where the already-committed words sit in each new snapshot (word alignment that ignores
///    case and punctuation, so "hello how" still matches "Hello, how"),
///  - treats every word after that point as pending,
///  - commits pending words only after they have been unchanged for the stabilization delay,
///  - never re-emits or retracts committed text (see the correction policy in the README).
///
/// Memory is bounded: it keeps at most <see cref="CaptionProcessorOptions.HistoryWords"/> committed
/// words plus the current snapshot. Not thread safe; call from one worker thread.
/// </summary>
public sealed class CaptionProcessor
{
    private readonly CaptionProcessorOptions _opt;

    private readonly List<Word> _committed = new();      // tail of committed words, bounded
    private readonly List<Pending> _pending = new();     // uncommitted words with first-seen times
    private Word[] _words = Array.Empty<Word>();         // current snapshot, tokenized
    private string? _snapshot;                           // current snapshot raw text
    private int _anchor;                                 // index in _words after the last committed word
    private TimeSpan _lastChange;
    private bool _baselineNext = true;                   // first snapshot is history, not new speech
    private bool _emittedAny;

    public CaptionProcessor(CaptionProcessorOptions? options = null)
    {
        _opt = options ?? new CaptionProcessorOptions();
    }

    public CaptionProcessorOptions Options => _opt;

    /// <summary>Words recognised but not yet committed, for the preview.</summary>
    public string PendingText => string.Join(' ', _pending.Select(p => p.Word.Raw));

    /// <summary>Times Live Captions rewrote text that was already committed (ignored by policy).</summary>
    public int CorrectionsIgnored { get; private set; }

    /// <summary>Times the snapshot no longer contained any committed text (cleared, restarted, or scrolled too far).</summary>
    public int Resets { get; private set; }

    public int CommittedHistoryCount => _committed.Count;

    /// <summary>
    /// Treat everything currently visible as already handled, without emitting it.
    /// Used when capture starts, when Auto Insert is enabled or resumed, and when the target textbox changes,
    /// so old captions never go into a newly focused field.
    /// </summary>
    public void SkipVisibleText()
    {
        if (_snapshot == null)
        {
            _baselineNext = true;
            return;
        }
        CommitWithoutEmitting(_words.Length);
    }

    /// <summary>The next emitted text starts without a leading space (new target).</summary>
    public void ResetOutputSpacing() => _emittedAny = false;

    /// <summary>True once any text was emitted since the last <see cref="ResetOutputSpacing"/>.</summary>
    public bool HasEmitted => _emittedAny;

    /// <summary>Continue an existing output (next text gets a leading space), e.g. after rebuilding the processor.</summary>
    public void ContinueOutput() => _emittedAny = true;

    /// <summary>
    /// Feed the latest caption text (call on every poll, even if unchanged, so stability timers advance).
    /// Returns newly committed text, or an empty string.
    /// </summary>
    public string Process(string? snapshot, TimeSpan now)
    {
        snapshot ??= string.Empty;
        var output = new StringBuilder();

        if (!string.Equals(snapshot, _snapshot, StringComparison.Ordinal))
        {
            _lastChange = now;
            OnSnapshotChanged(snapshot, output);
            if (_baselineNext)
            {
                _baselineNext = false;
                CommitWithoutEmitting(_words.Length);
                return output.ToString();
            }
            UpdatePending(now);
        }

        int count = CountCommittable(now);
        if (count > 0) Emit(count, output);
        return output.ToString();
    }

    // ---------------------------------------------------------------- snapshot handling

    private void OnSnapshotChanged(string snapshot, StringBuilder output)
    {
        var prevWords = _words;
        int prevAnchor = _anchor;

        _snapshot = snapshot;
        _words = Tokenize(snapshot, _opt.MaxSnapshotWords);

        if (_words.Length == 0)
        {
            // Empty or temporarily unavailable captions: keep history so we can realign later.
            _anchor = 0;
            _pending.Clear();
            return;
        }

        if (_committed.Count == 0)
        {
            _anchor = 0;
            return;
        }

        int expected = EstimateExpectedAnchor(prevWords, prevAnchor);
        var (anchor, exact) = Align(expected);
        if (anchor < 0)
        {
            // Nothing we committed is visible any more: new caption session or a full rewrite.
            Resets++;
            _anchor = 0;
            _pending.Clear();
            return;
        }

        _anchor = anchor;
        if (!exact) CorrectionsIgnored++;
        HandleLastWordChange(output);
    }

    /// <summary>
    /// If Live Captions appended punctuation to the last committed word ("go" -> "go."), emit just that
    /// punctuation. Any other change to committed words is ignored (we never delete user-visible text).
    /// </summary>
    private void HandleLastWordChange(StringBuilder output)
    {
        if (_anchor == 0) return;
        var seen = _words[_anchor - 1];
        var last = _committed[^1];
        if (seen.Key != last.Key || string.Equals(seen.Raw, last.Raw, StringComparison.Ordinal)) return;

        if (seen.Raw.Length > last.Raw.Length
            && seen.Raw.StartsWith(last.Raw, StringComparison.Ordinal)
            && IsAllPunctuation(seen.Raw.AsSpan(last.Raw.Length)))
        {
            output.Append(seen.Raw, last.Raw.Length, seen.Raw.Length - last.Raw.Length);
            _emittedAny = true;
        }
        else
        {
            CorrectionsIgnored++;
        }
        _committed[^1] = seen;
    }

    /// <summary>
    /// Where the committed text most likely ends in the new snapshot, assuming only lines scrolled off the top.
    /// Used to break ties between equally good matches (repeated phrases).
    /// </summary>
    private int EstimateExpectedAnchor(Word[] prevWords, int prevAnchor)
    {
        if (prevWords.Length == 0) return _words.Length;
        int k = Math.Min(4, _words.Length);
        for (int drop = 0; drop < prevWords.Length; drop++)
        {
            int n = Math.Min(k, prevWords.Length - drop);
            bool ok = true;
            for (int i = 0; i < n && ok; i++)
                ok = prevWords[drop + i].Key == _words[i].Key;
            if (ok) return prevAnchor - drop;
        }
        return prevAnchor;
    }

    /// <summary>
    /// Find the end of the committed words inside the current snapshot.
    ///
    /// Aligns the last AlignWindow committed words against the snapshot with a small edit-distance style
    /// dynamic program (match +2, substituted / missing / extra word -1). This tolerates Live Captions
    /// rewriting a committed word ("want" -> "wanted"), inserting or dropping one ("wanna" -> "want to"),
    /// and old words scrolling off the top (skipping committed words at the very start is free).
    /// Equal scores (repeated phrases) are resolved by the position expected from the previous snapshot.
    /// Returns (-1, false) when there is no reliable match.
    /// </summary>
    private (int anchor, bool exact) Align(int expected)
    {
        int w = Math.Min(_opt.AlignWindow, _committed.Count);
        int off = _committed.Count - w;
        int n = _words.Length;
        var prev = new int[n + 1]; // row i-1; row 0 is all zeros: the match may start anywhere
        var cur = new int[n + 1];

        for (int i = 1; i <= w; i++)
        {
            cur[0] = 0; // committed words before this point scrolled off the top
            string key = _committed[off + i - 1].Key;
            for (int j = 1; j <= n; j++)
            {
                int best = prev[j - 1] + (_words[j - 1].Key == key ? 2 : -1);
                best = Math.Max(best, prev[j] - 1);     // committed word no longer present
                best = Math.Max(best, cur[j - 1] - 1);  // extra word inserted by a correction
                cur[j] = best;
            }
            (prev, cur) = (cur, prev);
        }

        int bestScore = int.MinValue, bestEnd = -1;
        for (int j = 1; j <= n; j++)
        {
            int score = prev[j];
            if (score > bestScore || (score == bestScore && Math.Abs(j - expected) < Math.Abs(bestEnd - expected)))
            {
                bestScore = score;
                bestEnd = j;
            }
        }

        int minScore = w >= 3 ? 5 : 2 * w;
        if (bestEnd < 0 || bestScore < minScore) return (-1, false);
        return (bestEnd, bestScore == 2 * w);
    }

    private void UpdatePending(TimeSpan now)
    {
        int newCount = Math.Max(0, _words.Length - _anchor);
        int keep = 0;
        while (keep < _pending.Count && keep < newCount
               && string.Equals(_pending[keep].Word.Raw, _words[_anchor + keep].Raw, StringComparison.Ordinal))
            keep++;

        _pending.RemoveRange(keep, _pending.Count - keep);
        for (int i = keep; i < newCount; i++)
            _pending.Add(new Pending(_words[_anchor + i], now));
    }

    private int CountCommittable(TimeSpan now)
    {
        int stable = 0;
        while (stable < _pending.Count && now - _pending[stable].FirstSeen >= _opt.StabilizationDelay)
            stable++;
        if (stable == 0) return 0;

        if (_opt.Mode == CommitMode.StablePhrases) return stable;

        if (now - _lastChange >= _opt.SilenceFlush) return stable;
        for (int i = stable - 1; i >= 0; i--)
            if (EndsSentence(_pending[i].Word.Raw)) return i + 1;
        return 0;
    }

    private void Emit(int count, StringBuilder output)
    {
        for (int i = 0; i < count; i++)
        {
            if (_emittedAny || output.Length > 0) output.Append(' ');
            output.Append(_pending[i].Word.Raw);
            AddCommitted(_pending[i].Word);
        }
        _emittedAny = true;
        _pending.RemoveRange(0, count);
        _anchor += count;
    }

    private void CommitWithoutEmitting(int upTo)
    {
        for (int i = _anchor; i < upTo; i++) AddCommitted(_words[i]);
        _anchor = upTo;
        _pending.Clear();
    }

    private void AddCommitted(Word w)
    {
        _committed.Add(w);
        int extra = _committed.Count - _opt.HistoryWords;
        if (extra > 0) _committed.RemoveRange(0, extra);
    }

    // ---------------------------------------------------------------- text helpers

    internal static Word[] Tokenize(string text, int maxWords)
    {
        var list = new List<Word>();
        int i = 0;
        while (i < text.Length)
        {
            while (i < text.Length && char.IsWhiteSpace(text[i])) i++;
            int start = i;
            while (i < text.Length && !char.IsWhiteSpace(text[i])) i++;
            if (i > start)
            {
                string raw = text.Substring(start, i - start);
                list.Add(new Word(raw, MakeKey(raw)));
            }
        }
        if (list.Count > maxWords) list.RemoveRange(0, list.Count - maxWords);
        return list.ToArray();
    }

    /// <summary>Matching key: Unicode-normalised, lower case, punctuation removed. "¿Cómo," -> "cómo".</summary>
    internal static string MakeKey(string raw)
    {
        var norm = raw.Normalize(NormalizationForm.FormC);
        var sb = new StringBuilder(norm.Length);
        foreach (var ch in norm)
            if (!char.IsPunctuation(ch) && !char.IsSymbol(ch)) sb.Append(char.ToLowerInvariant(ch));
        // A token made only of punctuation (e.g. "-") keeps itself as key so it still aligns.
        return sb.Length > 0 ? sb.ToString() : norm;
    }

    internal static bool EndsSentence(string raw)
    {
        var s = raw.AsSpan().TrimEnd(")\"'»”’]");
        return s.Length > 0 && ".?!…。？！".Contains(s[^1]);
    }

    private static bool IsAllPunctuation(ReadOnlySpan<char> s)
    {
        foreach (var ch in s)
            if (!char.IsPunctuation(ch) && !char.IsSymbol(ch)) return false;
        return s.Length > 0;
    }

    internal readonly record struct Word(string Raw, string Key);

    private readonly record struct Pending(Word Word, TimeSpan FirstSeen);
}
