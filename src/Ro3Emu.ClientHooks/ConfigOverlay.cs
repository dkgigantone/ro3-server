using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using UnityEngine;

namespace Ro3Emu.ClientHooks
{
    /// <summary>
    /// Shows edited server values in the client. The server owns the real numbers (a card gives +40 Hit when the server's
    /// CardConfig says so), but the card window draws its effect list from the client's own copy of the table, which
    /// stays at the original +4. The server editor (plus-config-editor) writes the edited rows to
    /// &lt;client&gt;\ro3emu\custom\config-overlay.tsv, one line per row and field, tab separated:
    /// <c>Table  Row  Field  attr:value;attr:value</c> (a list of [attribute, value] pairs, the shape of CardConfig
    /// _kBasicAttribute and CardUpgradeConfig _iAttr). ShopCommodityConfig adds two more shapes, chosen by the field
    /// name: <c>_kItemPerPrice</c> is one flat [currency item id, price] list written <c>1:18000</c>, and
    /// <c>_iPurchaseNum</c> / <c>_iPurchaseTimesLimit</c> (and the other single-number fields, see KindOf) are plain numbers
    /// written <c>9</c>. A line <c>ShopCommodityConfig  NewRow  @clone  SourceRow</c> adds a new shop row: the source row is copied
    /// (with its metatable, which supplies the empty default fields the shop code reads), the field lines of the new row
    /// are applied, and the copy is stored under its id and appended to the tab's list (ShopItemsMap[class]); it is retried
    /// on every LuaPatches tick until the shop config is loaded. Lines starting with # are comments.
    /// <para>For each table in the file, GetEntryPtr is wrapped on the config class (the same route as WardrobeCustom,
    /// BaseClass's __newindex writes the shared vtbl, so every config object reaches the wrapper): the row the client asks
    /// for first gets the edited lines. The row is the client's live Lua table; nothing is written to disk and a restart
    /// returns the client to its own data. The rows already held by the config object found through Lua_DBManager are
    /// patched when the hook is installed. Everything is logged ("[Ro3Emu.ConfigOverlay]"), including a read-back of the
    /// first patched row, so a client where the window reads its data some other way shows up in the log.</para>
    /// </summary>
    internal static class ConfigOverlay
    {
        /// <summary>A config method called as cfg:GetEntryPtr(id) -> row table or nil.</summary>
        public delegate object EntryLookup(object self, object id);

        private const int MaxHitLogs = 6;

        /// <summary>The table names the overlay accepts (the client config classes are lua_cfg_&lt;lower case name&gt;).</summary>
        private static readonly string[] Tables = { "CardConfig", "CardUpgradeConfig", "ShopCommodityConfig" };

        // Kept alive here: Lua holds the pushed delegates only weakly through xLua's object pool.
        private static readonly List<TableHook> Hooks = new List<TableHook>();

        private static Obf_R.Obf_Ef _rawget, _rawset, _pack;

        /// <summary>How a field is stored in the client's row: a list of pairs, one flat pair, or a plain number.</summary>
        private enum Kind { Pairs, Flat, Number }

        private static readonly HashSet<string> NumberFields = new HashSet<string>
        {
            "_iPurchaseNum", "_iPurchaseTimesLimit", "_iItemId", "_iSortId", "_iDiscount", "_iMerchantDiscount", "_iIsBuy", "_iIsLock", "_iRecommendID",
        };

        private static Kind KindOf(string field) =>
            field == "_kItemPerPrice" ? Kind.Flat
            : NumberFields.Contains(field) ? Kind.Number
            : Kind.Pairs;

        /// <summary>The edited lines of one row, per field (Number: one line with one value; Flat: one line with two).</summary>
        private sealed class RowEdit
        {
            public readonly Dictionary<string, long[][]> Fields = new Dictionary<string, long[][]>();
            /// <summary>Above 0: this row does not exist yet; it is a copy of this source row (ShopCommodityConfig only).</summary>
            public long CloneFrom;
        }

        private sealed class PendingAdd
        {
            public long Id, From;
            public RowEdit Edit;
        }

        private static readonly List<PendingAdd> Adds = new List<PendingAdd>();
        private static Obf_R.Obf_Ef _next, _getmt, _setmt;
        private static Obf_R.Obf_ff _global;
        private static bool _addsDone, _addsGaveUp;
        private static int _addTries;

        private sealed class TableHook
        {
            public string Name;
            public Dictionary<long, RowEdit> Rows;
            public Obf_R.Obf_Ef Original;
            public EntryLookup Lookup;
            public int Hits;
            public bool ReadBack;

            public object Entry(object self, object id)
            {
                var row = First(Original?.Obf_HuA(self, id));
                try
                {
                    if (row is Obf_R.Obf_ff table && Rows.TryGetValue(LuaPatches.ToLong(id), out var edit))
                    {
                        bool changed = Apply(table, edit);
                        if (Hits++ < MaxHitLogs || changed) Log(Name + " " + id + ": GetEntryPtr hit, row patched now = " + changed);
                        if (!ReadBack) { ReadBack = true; Log(Name + " " + id + " reads back as " + Show(table, edit)); }
                    }
                }
                catch (Exception e) { Log(Name + " lookup wrapper: " + e.GetType().Name + ": " + e.Message); }
                return row;
            }
        }

        /// <summary>From LuaPatches.Tick, once per LuaEnv.</summary>
        internal static void Install(LA_LuaManager manager, Obf_R.Obf_ff global)
        {
            try
            {
                Hooks.Clear();
                Adds.Clear();
                _addsDone = false;
                _addTries = 0;
                var edits = Load();
                if (edits.Count == 0) return;
                _rawget = global.Obf_LuA<Obf_R.Obf_Ef>("rawget");
                _rawset = global.Obf_LuA<Obf_R.Obf_Ef>("rawset");
                _pack = global.Obf_LuA<Obf_R.Obf_Ef>("table.pack");
                _next = global.Obf_LuA<Obf_R.Obf_Ef>("next");
                _getmt = global.Obf_LuA<Obf_R.Obf_Ef>("getmetatable");
                _setmt = global.Obf_LuA<Obf_R.Obf_Ef>("setmetatable");
                _global = global;
                if (_rawget == null || _rawset == null || _pack == null) { Log("rawget/rawset/table.pack not found; nothing patched"); return; }
                if (edits.TryGetValue("ShopCommodityConfig", out var shopRows))
                    foreach (var pair in shopRows)
                        if (pair.Value.CloneFrom > 0) Adds.Add(new PendingAdd { Id = pair.Key, From = pair.Value.CloneFrom, Edit = pair.Value });
                if (Adds.Count > 0 && (_next == null || _getmt == null || _setmt == null)) { Log("next/getmetatable/setmetatable not found; shop rows not added"); Adds.Clear(); }
                var db = global.Obf_LuA<Obf_R.Obf_ff>("Lua_DBManager");
                var instance = db?.Obf_RuA<Obf_R.Obf_Ef>("GetInstance")?.Obf_HuA(db);
                var holder = instance != null && instance.Length > 0 ? instance[0] as Obf_R.Obf_ff : null;
                foreach (var pair in edits) Hook(manager, global, holder, pair.Key, pair.Value);
                if (Adds.Count > 0) { Log(Adds.Count + " shop row(s) to add"); TryAdd(); }
            }
            catch (Exception e) { Debug.LogWarning("[Ro3Emu.ConfigOverlay] " + e.GetType().Name + ": " + e.Message); }
        }

        /// <summary>From LuaPatches.Tick on every pass: adds the pending shop rows once the shop config is loaded.</summary>
        internal static void Retry()
        {
            if (Adds.Count == 0) return;
            // Not done after the first success: the game may rebuild the tab lists later (login, reload) and drop the copies,
            // so every pass checks that each row is still in its tab list and puts it back when it is not.
            if (!_addsDone && ++_addTries > 400) { _addsDone = true; Log("shop rows: the shop config never became ready; rows not added"); return; }
            if (_addsGaveUp) return;
            TryAdd();
        }

        private static void TryAdd()
        {
            try
            {
                var db = _global?.Obf_LuA<Obf_R.Obf_ff>("Lua_DBManager");
                var instance = db?.Obf_RuA<Obf_R.Obf_Ef>("GetInstance")?.Obf_HuA(db);
                var holder = instance != null && instance.Length > 0 ? instance[0] as Obf_R.Obf_ff : null;
                var cfg = holder?.Obf_RuA<object>("m_kCFG_ShopCommodityConfig") as Obf_R.Obf_ff;
                var map = cfg?.Obf_RuA<object>("ShopItemsMap") as Obf_R.Obf_ff;
                var values = _global?.Obf_LuA<Obf_R.Obf_ff>("package.loaded.shopcommodityconfig")?.Obf_RuA<object>("m_kValues") as Obf_R.Obf_ff;
                if (map == null || values == null) return; // not loaded yet; the next tick tries again
                foreach (var add in Adds) AddRow(map, values, add);
                _addsDone = true;
            }
            catch (Exception e) { _addsDone = true; _addsGaveUp = true; Log("add shop rows: " + e.GetType().Name + ": " + e.Message); }
        }

        private static void AddRow(Obf_R.Obf_ff map, Obf_R.Obf_ff values, PendingAdd add)
        {
            var existing = First(_rawget.Obf_HuA(values, add.Id)) as Obf_R.Obf_ff;
            var src = First(_rawget.Obf_HuA(values, add.From)) as Obf_R.Obf_ff ?? FindRow(map, add.From);
            if (src == null) { Log("shop row " + add.Id + ": source row " + add.From + " not found; not added"); return; }
            var cls = LuaPatches.ToLong(First(_rawget.Obf_HuA(src, "_iClassID")));
            if (!(First(_rawget.Obf_HuA(map, cls)) is Obf_R.Obf_ff list)) { Log("shop row " + add.Id + ": no tab list for class " + cls + "; not added"); return; }
            if (existing != null)
            {
                // Row kept, but the tab list may have been rebuilt without it.
                long k = 0;
                for (; First(_rawget.Obf_HuA(list, k + 1)) is Obf_R.Obf_ff r; k++)
                    if (ReferenceEquals(r, existing) || LuaPatches.ToLong(First(_rawget.Obf_HuA(r, "_iID"))) == add.Id) return;
                _rawset.Obf_HuA(list, k + 1, existing);
                Log("shop row " + add.Id + " was missing from class " + cls + " (list rebuilt); put back, list now " + (k + 1) + " long");
                return;
            }
            var row = NewTable();
            object key = null;
            for (var guard = 0; guard < 500; guard++)
            {
                var pair = _next.Obf_HuA(src, key);
                if (pair == null || pair.Length < 2 || pair[0] == null) break;
                key = pair[0];
                _rawset.Obf_HuA(row, key, pair[1]);
            }
            var meta = _getmt.Obf_HuA(src);
            if (meta != null && meta.Length > 0 && meta[0] != null) _setmt.Obf_HuA(row, meta[0]);
            _rawset.Obf_HuA(row, "_iID", add.Id);
            Apply(row, add.Edit);
            _rawset.Obf_HuA(values, add.Id, row);
            long n = 0;
            while (First(_rawget.Obf_HuA(list, n + 1)) != null) n++;
            _rawset.Obf_HuA(list, n + 1, row);
            Log("shop row " + add.Id + " added to class " + cls + " (copy of " + add.From + "), list now " + (n + 1) + " long; " + Show(row, add.Edit));
        }

        private static Obf_R.Obf_ff FindRow(Obf_R.Obf_ff map, long id)
        {
            object key = null;
            for (var g = 0; g < 200; g++)
            {
                var pair = _next.Obf_HuA(map, key);
                if (pair == null || pair.Length < 2 || pair[0] == null) break;
                key = pair[0];
                if (!(pair[1] is Obf_R.Obf_ff list)) continue;
                for (long i = 1; First(_rawget.Obf_HuA(list, i)) is Obf_R.Obf_ff r; i++)
                    if (LuaPatches.ToLong(First(_rawget.Obf_HuA(r, "_iID"))) == id) return r;
            }
            return null;
        }

        private static void Hook(LA_LuaManager manager, Obf_R.Obf_ff global, Obf_R.Obf_ff holder, string table, Dictionary<long, RowEdit> rows)
        {
            var config = LuaPatches.Module(manager, global, "lua_cfg_" + table.ToLowerInvariant());
            var original = config?.Obf_RuA<Obf_R.Obf_Ef>("GetEntryPtr");
            if (original == null) { Log(table + ": config class or GetEntryPtr not found; not wrapped"); return; }
            var hook = new TableHook { Name = table, Rows = rows, Original = original };
            hook.Lookup = hook.Entry;
            Hooks.Add(hook);
            config.Obf_luA<string, EntryLookup>("GetEntryPtr", hook.Lookup);
            Log(table + ": " + rows.Count + " edited row(s); GetEntryPtr wrapped on the config class");
            // Rows of the config object the game already holds: patch them now (the wrapper covers the rest).
            if (!(holder?.Obf_RuA<object>("m_kCFG_" + table) is Obf_R.Obf_ff instance)) { Log(table + ": m_kCFG_" + table + " not found yet; the wrapper patches rows as they are read"); return; }
            foreach (var row in rows)
                if (First(original.Obf_HuA(instance, row.Key)) is Obf_R.Obf_ff found)
                    Log(table + " " + row.Key + ": patched at install = " + Apply(found, row.Value));
        }

        /// <summary>Gives the row the edited lines; false when it already had them.</summary>
        private static bool Apply(Obf_R.Obf_ff row, RowEdit edit)
        {
            bool changed = false;
            foreach (var field in edit.Fields)
            {
                if (Matches(row, field.Key, field.Value)) continue;
                if (KindOf(field.Key) == Kind.Number) _rawset.Obf_HuA(row, field.Key, field.Value[0][0]);
                else _rawset.Obf_HuA(row, field.Key, Build(field.Value, KindOf(field.Key) == Kind.Flat));
                changed = true;
            }
            return changed;
        }

        private static bool Matches(Obf_R.Obf_ff row, string field, long[][] lines)
        {
            var current = row.Obf_RuA<object>(field);
            var kind = KindOf(field);
            if (kind == Kind.Number) return current != null && !(current is Obf_R.Obf_ff) && LuaPatches.ToLong(current) == lines[0][0];
            var outer = current as Obf_R.Obf_ff;
            if (outer == null) return false;
            if (kind == Kind.Flat)
                return LuaPatches.ToLong(First(_rawget.Obf_HuA(outer, 1L))) == lines[0][0]
                    && LuaPatches.ToLong(First(_rawget.Obf_HuA(outer, 2L))) == lines[0][1]
                    && First(_rawget.Obf_HuA(outer, 3L)) == null;
            for (var i = 0; i < lines.Length; i++)
            {
                if (!(First(_rawget.Obf_HuA(outer, i + 1L)) is Obf_R.Obf_ff inner)) return false;
                if (LuaPatches.ToLong(First(_rawget.Obf_HuA(inner, 1L))) != lines[i][0]) return false;
                if (LuaPatches.ToLong(First(_rawget.Obf_HuA(inner, 2L))) != lines[i][1]) return false;
            }
            return First(_rawget.Obf_HuA(outer, lines.Length + 1L)) == null;
        }

        /// <summary>A Lua list of {attribute, value} lists (or, flat, one {a, b} list), built like the client's own (table.pack without n).</summary>
        private static Obf_R.Obf_ff Build(long[][] lines, bool flat = false)
        {
            var outer = NewTable();
            if (flat)
            {
                _rawset.Obf_HuA(outer, 1L, lines[0][0]);
                _rawset.Obf_HuA(outer, 2L, lines[0][1]);
                return outer;
            }
            for (var i = 0; i < lines.Length; i++)
            {
                var inner = NewTable();
                _rawset.Obf_HuA(inner, 1L, lines[i][0]);
                _rawset.Obf_HuA(inner, 2L, lines[i][1]);
                _rawset.Obf_HuA(outer, i + 1L, inner);
            }
            return outer;
        }

        private static Obf_R.Obf_ff NewTable()
        {
            var table = (Obf_R.Obf_ff)_pack.Obf_HuA()[0];
            _rawset.Obf_HuA(table, "n", null);
            _rawset.Obf_HuA(table, "__paramslen", null);
            return table;
        }

        private static string Show(Obf_R.Obf_ff row, RowEdit edit)
        {
            var text = new System.Text.StringBuilder();
            foreach (var field in edit.Fields.Keys)
            {
                text.Append(field).Append(" =");
                var now = row.Obf_RuA<object>(field);
                if (KindOf(field) == Kind.Number) text.Append(' ').Append(now == null || now is Obf_R.Obf_ff ? "nil" : LuaPatches.ToLong(now).ToString(CultureInfo.InvariantCulture));
                else if (KindOf(field) == Kind.Flat && now is Obf_R.Obf_ff flat)
                    text.Append(' ').Append(LuaPatches.ToLong(First(_rawget.Obf_HuA(flat, 1L)))).Append(':').Append(LuaPatches.ToLong(First(_rawget.Obf_HuA(flat, 2L))));
                else if (now is Obf_R.Obf_ff outer)
                    for (var i = 1L; First(_rawget.Obf_HuA(outer, i)) is Obf_R.Obf_ff inner; i++)
                        text.Append(' ').Append(LuaPatches.ToLong(First(_rawget.Obf_HuA(inner, 1L)))).Append(':').Append(LuaPatches.ToLong(First(_rawget.Obf_HuA(inner, 2L))));
                else text.Append(" nil");
                text.Append("; ");
            }
            return text.ToString();
        }

        /// <summary>The overlay file: &lt;client&gt;\ro3emu\custom\config-overlay.tsv.</summary>
        private static Dictionary<string, Dictionary<long, RowEdit>> Load()
        {
            var all = new Dictionary<string, Dictionary<long, RowEdit>>();
            var path = Path.Combine(Path.GetDirectoryName(Application.dataPath), "ro3emu", "custom", "config-overlay.tsv");
            if (!File.Exists(path)) { Log("no overlay at " + path + " (nothing edited, or not copied from the editor)"); return all; }
            foreach (var line in File.ReadAllLines(path, System.Text.Encoding.UTF8))
            {
                if (line.Length == 0 || line[0] == '#') continue;
                var c = line.Split('\t');
                if (c.Length >= 4 && c[2] == "@clone" && Array.IndexOf(Tables, c[0]) >= 0
                    && long.TryParse(c[1], NumberStyles.Integer, CultureInfo.InvariantCulture, out var newRow)
                    && long.TryParse(c[3], NumberStyles.Integer, CultureInfo.InvariantCulture, out var from) && from > 0)
                {
                    if (!all.TryGetValue(c[0], out var cloneRows)) all[c[0]] = cloneRows = new Dictionary<long, RowEdit>();
                    if (!cloneRows.TryGetValue(newRow, out var cloneEdit)) cloneRows[newRow] = cloneEdit = new RowEdit();
                    cloneEdit.CloneFrom = from;
                    continue;
                }
                if (c.Length < 4 || Array.IndexOf(Tables, c[0]) < 0 || !long.TryParse(c[1], NumberStyles.Integer, CultureInfo.InvariantCulture, out var row) || !TryField(c[2], c[3], out var lines))
                {
                    Log("bad line in config-overlay.tsv: " + line);
                    continue;
                }
                if (!all.TryGetValue(c[0], out var rows)) all[c[0]] = rows = new Dictionary<long, RowEdit>();
                if (!rows.TryGetValue(row, out var edit)) rows[row] = edit = new RowEdit();
                edit.Fields[c[2]] = lines;
            }
            return all;
        }

        /// <summary>Reads a field's text by its shape: "9" (number), "1:18000" (flat pair) or "104:40;1:10" (list of pairs).</summary>
        private static bool TryField(string field, string text, out long[][] lines)
        {
            lines = null;
            var kind = KindOf(field);
            if (kind == Kind.Number)
            {
                if (!long.TryParse(text.Trim(), NumberStyles.Integer, CultureInfo.InvariantCulture, out var n)) return false;
                lines = new[] { new[] { n } };
                return true;
            }
            if (!TryLines(text, out lines)) return false;
            return kind != Kind.Flat || lines.Length == 1;
        }

        /// <summary>"104:40;1:10" -> [[104, 40], [1, 10]]; an empty text is an empty list.</summary>
        private static bool TryLines(string text, out long[][] lines)
        {
            var list = new List<long[]>();
            lines = null;
            foreach (var part in text.Split(new[] { ';' }, StringSplitOptions.RemoveEmptyEntries))
            {
                var two = part.Split(':');
                if (two.Length != 2 || !long.TryParse(two[0], NumberStyles.Integer, CultureInfo.InvariantCulture, out var a)
                    || !long.TryParse(two[1], NumberStyles.Integer, CultureInfo.InvariantCulture, out var v)) return false;
                list.Add(new[] { a, v });
            }
            lines = list.ToArray();
            return true;
        }

        private static object First(object[] result) => result != null && result.Length > 0 ? result[0] : null;

        private static void Log(string message) => Debug.Log("[Ro3Emu.ConfigOverlay] " + message);
    }
}
