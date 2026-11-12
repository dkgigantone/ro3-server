using System.Text.Json;
using Microsoft.Extensions.Options;
using Tencent.IM;

namespace Ro3Emu.Server.Game;

/// <summary>
/// Private (friend) chat history, kept for <see cref="Retention"/> (7 days) and then removed by the server. Whispers
/// used to reach only a character that was online at that moment and the history request was answered empty, so a
/// friend who logged in later saw nothing, including the gift card the client sends as a whisper after a fashion gift
/// (see <see cref="ShopService"/> gifting). Each line is one JSON object in <c>DataDir/private-chat.jsonl</c>; the file
/// is read once, kept in memory and rewritten only when lines are removed or marked delivered.
/// </summary>
public sealed partial class PrivateChatStore(IOptions<ServerOptions> options, TimeProvider clock, ILogger<PrivateChatStore> log)
    : BackgroundService
{
    /// <summary>Lines older than this are deleted by the hourly cleanup.</summary>
    internal static readonly TimeSpan Retention = TimeSpan.FromDays(7);

    private static readonly TimeSpan CleanupInterval = TimeSpan.FromHours(1);

    /// <summary>Most lines one history reply carries.</summary>
    internal const int MaxHistoryLines = 100;

    internal sealed class Line
    {
        public string Id { get; set; } = "";
        public long From { get; set; }
        public long To { get; set; }
        public long Time { get; set; }
        public string Content { get; set; } = "";
        public string Extra { get; set; } = "";
        public bool Delivered { get; set; }
    }

    private readonly object _sync = new();
    private readonly List<Line> _lines = [];
    private bool _loaded;

    private string FilePath => Path.Combine(options.Value.DataDir, "private-chat.jsonl");

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        while (!stoppingToken.IsCancellationRequested)
        {
            try { Prune(); }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException) { LogCleanupFailed(log, e); }
            try { await Task.Delay(CleanupInterval, clock, stoppingToken); } catch (OperationCanceledException) { return; }
        }
    }

    /// <summary>Stores one whisper; <paramref name="delivered"/> is true when the recipient was online and got the push.</summary>
    public void Add(TextChatMessage message, long from, long to, bool delivered)
    {
        var line = new Line
        {
            Id = message.MessageId, From = from, To = to, Time = (long)message.SendTime,
            Content = message.Content, Extra = message.ExtraParam, Delivered = delivered,
        };
        lock (_sync)
        {
            EnsureLoaded();
            _lines.Add(line);
            try { File.AppendAllText(FilePath, JsonSerializer.Serialize(line) + "\n"); }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException) { LogWriteFailed(log, e); }
        }
    }

    /// <summary>The newest <paramref name="count"/> lines between two roles, oldest first.</summary>
    public List<TextChatMessage> History(long a, long b, int count)
    {
        int take = Math.Clamp(count, 1, MaxHistoryLines);
        lock (_sync)
        {
            EnsureLoaded();
            long cutoff = clock.GetUtcNow().Add(-Retention).ToUnixTimeSeconds();
            return [.. _lines.Where(l => l.Time >= cutoff && ((l.From == a && l.To == b) || (l.From == b && l.To == a)))
                .TakeLast(take).Select(ToMessage)];
        }
    }

    /// <summary>Whispers sent to <paramref name="to"/> while it was offline, marked delivered so each is pushed once.</summary>
    public List<TextChatMessage> TakeUndelivered(long to)
    {
        lock (_sync)
        {
            EnsureLoaded();
            long cutoff = clock.GetUtcNow().Add(-Retention).ToUnixTimeSeconds();
            var pending = _lines.Where(l => l.To == to && !l.Delivered && l.Time >= cutoff).ToList();
            if (pending.Count == 0) return [];
            foreach (var line in pending) line.Delivered = true;
            try { Rewrite(); }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException) { LogWriteFailed(log, e); }
            return [.. pending.Select(ToMessage)];
        }
    }

    /// <summary>Removes every line older than the retention; returns how many were removed.</summary>
    internal int Prune()
    {
        lock (_sync)
        {
            EnsureLoaded();
            long cutoff = clock.GetUtcNow().Add(-Retention).ToUnixTimeSeconds();
            int removed = _lines.RemoveAll(l => l.Time < cutoff);
            if (removed > 0)
            {
                Rewrite();
                LogPruned(log, removed);
            }
            return removed;
        }
    }

    private static TextChatMessage ToMessage(Line line) => new()
    {
        MessageId = line.Id, SenderId = line.From.ToString(System.Globalization.CultureInfo.InvariantCulture),
        ReceiveId = line.To.ToString(System.Globalization.CultureInfo.InvariantCulture),
        SendTime = (ulong)line.Time, Content = line.Content, ExtraParam = line.Extra,
    };

    /// <summary>Caller holds the lock.</summary>
    private void EnsureLoaded()
    {
        if (_loaded) return;
        _loaded = true;
        string path = FilePath;
        if (!File.Exists(path)) return;
        try
        {
            foreach (string text in File.ReadLines(path))
            {
                if (string.IsNullOrWhiteSpace(text)) continue;
                try { if (JsonSerializer.Deserialize<Line>(text) is { } line) _lines.Add(line); }
                catch (JsonException) { /* one damaged line must not lose the rest of the history */ }
            }
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException) { LogWriteFailed(log, e); }
    }

    /// <summary>Caller holds the lock: the whole file is replaced through a temporary copy.</summary>
    private void Rewrite()
    {
        string path = FilePath, temp = path + ".tmp";
        File.WriteAllLines(temp, _lines.Select(l => JsonSerializer.Serialize(l)));
        File.Move(temp, path, overwrite: true);
    }

    [LoggerMessage(Level = LogLevel.Information, Message = "Removed {Count} private chat line(s) older than 7 days")]
    private static partial void LogPruned(ILogger logger, int count);

    [LoggerMessage(Level = LogLevel.Warning, Message = "Private chat history could not be read or written; chat keeps working without it")]
    private static partial void LogWriteFailed(ILogger logger, Exception exception);

    [LoggerMessage(Level = LogLevel.Warning, Message = "Private chat cleanup failed; it runs again in an hour")]
    private static partial void LogCleanupFailed(ILogger logger, Exception exception);
}
