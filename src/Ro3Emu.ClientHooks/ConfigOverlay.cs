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
    /// _kBasicAttribute and CardUpgradeConfig _iAttr). Lines starting with # are comments.
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
        private static readonly string[] Tables = { "CardConfig", "CardUpgradeConfig" };

        // Kept alive here: Lua holds the pushed delegates only weakly through xLua's object pool.
        private static readonly List<TableHook> Hooks = new List<TableHook>();

        private static Obf_R.Obf_Ef _rawget, _rawset, _pack;

        /// <summary>The edited lines of one row, per field.</summary>
        private sealed class RowEdit
        {
            public readonly Dictionary<string, long[][]> Fields = new Dictionary<string, long[][]>();
        }

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
                var edits = Load();
                if (edits.Count == 0) return;
                _rawget = global.Obf_LuA<Obf_R.Obf_Ef>("rawget");
                _rawset = global.Obf_LuA<Obf_R.Obf_Ef>("rawset");
                _pack = global.Obf_LuA<Obf_R.Obf_Ef>("table.pack");
                if (_rawget == null || _rawset == null || _pack == null) { Log("rawget/rawset/table.pack not found; nothing patched"); return; }
                var db = global.Obf_LuA<Obf_R.Obf_ff>("Lua_DBManager");
                var instance = db?.Obf_RuA<Obf_R.Obf_Ef>("GetInstance")?.Obf_HuA(db);
                var holder = instance != null && instance.Length > 0 ? instance[0] as Obf_R.Obf_ff : null;
                foreach (var pair in edits) Hook(manager, global, holder, pair.Key, pair.Value);
            }
            catch (Exception e) { Debug.LogWarning("[Ro3Emu.ConfigOverlay] " + e.GetType().Name + ": " + e.Message); }
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
                if (Matches(row.Obf_RuA<object>(field.Key) as Obf_R.Obf_ff, field.Value)) continue;
                _rawset.Obf_HuA(row, field.Key, Build(field.Value));
                changed = true;
            }
            return changed;
        }

        private static bool Matches(Obf_R.Obf_ff outer, long[][] lines)
        {
            if (outer == null) return false;
            for (var i = 0; i < lines.Length; i++)
            {
                if (!(First(_rawget.Obf_HuA(outer, i + 1L)) is Obf_R.Obf_ff inner)) return false;
                if (LuaPatches.ToLong(First(_rawget.Obf_HuA(inner, 1L))) != lines[i][0]) return false;
                if (LuaPatches.ToLong(First(_rawget.Obf_HuA(inner, 2L))) != lines[i][1]) return false;
            }
            return First(_rawget.Obf_HuA(outer, lines.Length + 1L)) == null;
        }

        /// <summary>A Lua list of {attribute, value} lists, built like the client's own (table.pack without n).</summary>
        private static Obf_R.Obf_ff Build(long[][] lines)
        {
            var outer = NewTable();
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
                if (row.Obf_RuA<object>(field) is Obf_R.Obf_ff outer)
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
                if (c.Length < 4 || Array.IndexOf(Tables, c[0]) < 0 || !long.TryParse(c[1], NumberStyles.Integer, CultureInfo.InvariantCulture, out var row) || !TryLines(c[3], out var lines))
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
