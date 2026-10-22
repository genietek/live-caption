using System.Text;
using CaptionBridge.Core;
using Xunit;

namespace CaptionBridge.Tests;

/// <summary>Drives a CaptionProcessor with a mock caption stream and a fake clock.</summary>
internal sealed class Feed
{
    public readonly CaptionProcessor P;
    public TimeSpan Now;
    public readonly StringBuilder Out = new();
    private string _last = "";

    public Feed(CaptionProcessorOptions? opt = null, string initial = "")
    {
        P = new CaptionProcessor(opt ?? new CaptionProcessorOptions { StabilizationDelay = TimeSpan.FromMilliseconds(500) });
        Step(initial);
    }

    public int DelayMs => (int)P.Options.StabilizationDelay.TotalMilliseconds;

    public string Step(string snapshot, int advanceMs = 0)
    {
        Now += TimeSpan.FromMilliseconds(advanceMs);
        _last = snapshot;
        var s = P.Process(snapshot, Now);
        Out.Append(s);
        return s;
    }

    /// <summary>Show a snapshot, then keep polling it until the stabilization delay has passed.</summary>
    public string Settle(string snapshot)
    {
        var a = Step(snapshot, 100);
        var b = Step(snapshot, DelayMs);
        return a + b;
    }

    public string Wait(int ms) => Step(_last, ms);

    public string Text => Out.ToString();
}

public class CaptionProcessorTests
{
    [Fact]
    public void Text_already_visible_at_start_is_not_emitted()
    {
        var f = new Feed(initial: "Some old caption from before we started.");
        f.Wait(5000);
        Assert.Equal("", f.Text);
    }

    [Fact]
    public void Emits_only_the_new_words_when_caption_grows()
    {
        var f = new Feed();
        Assert.Equal("Hello how are", f.Settle("Hello how are"));
        Assert.Equal(" you today", f.Settle("Hello how are you today"));
        Assert.Equal("Hello how are you today", f.Text);
    }

    [Fact]
    public void Unstable_words_wait_for_the_stabilization_delay()
    {
        var f = new Feed();
        Assert.Equal("", f.Step("Hello", 100));
        Assert.Equal("", f.Step("Hello", 300));
        Assert.Equal("Hello", f.Step("Hello", 300));
    }

    [Fact]
    public void Partial_results_are_replaced_before_commit()
    {
        var f = new Feed();
        f.Step("I want to go", 100);
        f.Step("I wanted to go", 200);   // revised before the delay passed
        f.Wait(f.DelayMs);
        Assert.Equal("I wanted to go", f.Text);
    }

    [Fact]
    public void Each_word_commits_on_its_own_timer()
    {
        var f = new Feed();
        f.Step("one two", 100);
        f.Step("one two three", 300);
        Assert.Equal("one two", f.Step("one two three", 250));
        Assert.Equal(" three", f.Step("one two three", 300));
    }

    [Fact]
    public void Correction_of_committed_text_is_not_reinserted_and_does_not_duplicate()
    {
        var f = new Feed();
        f.Settle("I want to go");
        Assert.Equal("", f.Settle("I wanted to go"));
        Assert.True(f.P.CorrectionsIgnored >= 1);
        Assert.Equal(" home", f.Settle("I wanted to go home"));
        Assert.Equal("I want to go home", f.Text);
    }

    [Fact]
    public void Word_split_or_merge_in_committed_text_is_tolerated()
    {
        var f = new Feed();
        f.Settle("we are gonna be late");
        Assert.Equal("", f.Settle("we are going to be late"));
        Assert.Equal(" again", f.Settle("we are going to be late again"));
    }

    [Fact]
    public void Punctuation_and_case_fixes_to_committed_words_are_ignored()
    {
        var f = new Feed();
        f.Settle("hello how are you");
        Assert.Equal("", f.Settle("Hello, how are you"));
        Assert.Equal(" doing", f.Settle("Hello, how are you doing"));
        Assert.Equal("hello how are you doing", f.Text);
    }

    [Fact]
    public void Punctuation_appended_to_last_committed_word_is_emitted()
    {
        var f = new Feed();
        f.Settle("I went home");
        Assert.Equal(".", f.Settle("I went home."));
        Assert.Equal(" Then", f.Settle("I went home. Then"));
        Assert.Equal("I went home. Then", f.Text);
    }

    [Fact]
    public void Sliding_window_old_lines_scroll_off_the_top()
    {
        var f = new Feed();
        f.Settle("alpha bravo charlie delta echo");
        f.Settle("alpha bravo charlie delta echo foxtrot golf");
        // first three words scroll away, two new ones arrive in the same update
        Assert.Equal(" hotel india", f.Settle("delta echo foxtrot golf hotel india"));
        Assert.Equal("alpha bravo charlie delta echo foxtrot golf hotel india", f.Text);
    }

    [Fact]
    public void Repeated_phrases_are_kept_and_not_duplicated()
    {
        var f = new Feed();
        f.Settle("thank you");
        Assert.Equal(" thank you", f.Settle("thank you thank you"));
        Assert.Equal(" thank you", f.Settle("thank you thank you thank you"));
        Assert.Equal("", f.Wait(2000));
        Assert.Equal("thank you thank you thank you", f.Text);
    }

    [Fact]
    public void Repeated_single_word_counts_correctly()
    {
        var f = new Feed();
        f.Settle("yes");
        Assert.Equal(" yes", f.Settle("yes yes"));
        Assert.Equal(" no", f.Settle("yes yes no"));
        Assert.Equal("yes yes no", f.Text);
    }

    [Fact]
    public void Repeated_phrase_with_scrolling_does_not_duplicate()
    {
        var f = new Feed();
        f.Settle("I said thank you and she said thank you");
        // the first line scrolls away and "thank you" is said once more in the same update
        Assert.Equal(" thank you", f.Settle("and she said thank you thank you"));
        Assert.Equal("I said thank you and she said thank you thank you", f.Text);
    }

    [Fact]
    public void Spanish_unicode_is_preserved_exactly()
    {
        var f = new Feed();
        f.Settle("¿Cómo estás?");
        f.Settle("¿Cómo estás? Mañana, ¡qué bien!");
        f.Settle("¿Cómo estás? Mañana, ¡qué bien! Año pingüino ÑANDÚ");
        Assert.Equal("¿Cómo estás? Mañana, ¡qué bien! Año pingüino ÑANDÚ", f.Text);
    }

    [Fact]
    public void Unicode_normalization_differences_still_align()
    {
        var f = new Feed();
        f.Settle("café con leche"); // decomposed é
        Assert.Equal(" por favor", f.Settle("café con leche por favor")); // precomposed é
    }

    [Fact]
    public void Empty_caption_then_new_speech_is_emitted()
    {
        var f = new Feed();
        f.Settle("first sentence here");
        Assert.Equal("", f.Settle(""));              // captions cleared / temporarily unavailable
        Assert.Equal(" brand new words", f.Settle("brand new words"));
        Assert.Equal(1, f.P.Resets);
    }

    [Fact]
    public void Temporarily_unavailable_then_same_text_does_not_duplicate()
    {
        var f = new Feed();
        f.Settle("the quick brown fox");
        f.Settle("");
        Assert.Equal(" jumps", f.Settle("the quick brown fox jumps"));
        Assert.Equal("the quick brown fox jumps", f.Text);
    }

    [Fact]
    public void Completely_different_text_is_treated_as_a_reset()
    {
        var f = new Feed();
        f.Settle("one two three four");
        Assert.Equal(" alpha beta gamma", f.Settle("alpha beta gamma"));
        Assert.Equal(1, f.P.Resets);
    }

    [Fact]
    public void SkipVisibleText_drops_pending_and_visible_words()
    {
        var f = new Feed();
        f.Settle("keep this");
        f.Step("keep this but not this", 100);
        f.P.SkipVisibleText();       // e.g. the user focused another textbox
        f.P.ResetOutputSpacing();
        f.Wait(2000);
        Assert.Equal("keep this", f.Text);
        Assert.Equal("only new", f.Settle("keep this but not this only new"));
    }

    [Fact]
    public void Sentence_mode_commits_complete_sentences_then_flushes_after_silence()
    {
        var f = new Feed(new CaptionProcessorOptions
        {
            StabilizationDelay = TimeSpan.FromMilliseconds(300),
            Mode = CommitMode.CompleteSentences,
            SilenceFlush = TimeSpan.FromMilliseconds(1500),
        });
        f.Step("It is late. We should", 100);
        f.Step("It is late. We should go", 400);
        Assert.Equal("It is late.", f.Text);
        f.Step("It is late. We should go", 600);
        Assert.Equal("It is late.", f.Text);
        f.Wait(1000); // 1600 ms of no change
        Assert.Equal("It is late. We should go", f.Text);
    }

    [Fact]
    public void Sentence_mode_handles_spanish_question_and_exclamation()
    {
        var f = new Feed(new CaptionProcessorOptions
        {
            StabilizationDelay = TimeSpan.FromMilliseconds(200),
            Mode = CommitMode.CompleteSentences,
        });
        f.Step("¿Vienes mañana? ¡Claro! Pero", 100);
        f.Wait(300);
        Assert.Equal("¿Vienes mañana? ¡Claro!", f.Text);
    }

    [Fact]
    public void Long_running_sliding_window_stream_emits_every_word_exactly_once_with_bounded_memory()
    {
        var rng = new Random(1234);
        string[] vocab = { "the", "a", "and", "yes", "no", "really", "okay", "so", "we", "go", "to", "it", "año", "¿qué?" };
        var opt = new CaptionProcessorOptions { StabilizationDelay = TimeSpan.FromMilliseconds(400), HistoryWords = 64 };
        var f = new Feed(opt);
        var spoken = new List<string>();
        var window = new List<string>();

        for (int i = 0; i < 5000; i++)
        {
            var w = vocab[rng.Next(vocab.Length)];
            spoken.Add(w);
            window.Add(w);
            if (window.Count > 30) window.RemoveRange(0, rng.Next(1, 8) + (window.Count - 30));
            f.Step(string.Join(' ', window), 250);
            Assert.True(f.P.CommittedHistoryCount <= 64);
        }
        f.Wait(1000);

        Assert.Equal(string.Join(' ', spoken), f.Text);
        Assert.Equal(0, f.P.Resets);
    }

    [Fact]
    public void Polling_the_same_snapshot_is_cheap_and_emits_nothing_new()
    {
        var f = new Feed();
        f.Settle("steady text");
        for (int i = 0; i < 1000; i++) Assert.Equal("", f.Wait(200));
        Assert.Equal("steady text", f.Text);
    }

    [Theory]
    [InlineData("hello.", true)]
    [InlineData("¿qué?", true)]
    [InlineData("¡vamos!\"", true)]
    [InlineData("so…", true)]
    [InlineData("hello,", false)]
    [InlineData("hello", false)]
    public void Sentence_end_detection(string word, bool ends) =>
        Assert.Equal(ends, CaptionProcessor.EndsSentence(word));
}
