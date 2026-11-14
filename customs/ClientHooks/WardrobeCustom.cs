using System;
using UnityEngine;

namespace Ro3Emu.ClientHooks
{
    /// <summary>
    /// Custom wardrobe entries: for each hat in Hats adds a copy of the Banana Hat's WardRobeConfig row under a new ID
    /// (WardId) to the client's live Lua config and to the headgear ID list the wardrobe builds from
    /// (m_kWardRodeTypeInfo[1]). Because the window may hold another copy of the config object than the one found
    /// through Lua_DBManager, GetWardRodeTypeInfo is also wrapped on the config class: whichever object is asked gets
    /// the row first. Everything is logged ("[Ro3Emu.WardrobeCustom]"). The model, icon and name are still the
    /// Banana Hat's. Nothing is written to disk; a restart returns the client to normal.
    /// </summary>
    internal static class WardrobeCustom
    {
        /// <summary>A config method called as cfg:Method(type) -> list.</summary>
        public delegate object TypeLookup(object self, object type);

        /// <summary>Language.Trans(key, params) -> text; key is a text ID or a table {ID}.</summary>
        public delegate object TransLookup(object key, object param);

        private const int MaxCallLogs = 8;

        /// <summary>One custom hat: the item that unlocks the look, the wardrobe look, its texts and the rows built from Banana's.</summary>
        private sealed class Hat
        {
            internal Hat(long itemId, long wardId, string asset, string name, long textBase, string wardDesc, string itemDesc, long style, long rarity, bool stall, long price, string slot, long sourceWard, long sourceItem, string token, long typeIndex)
            {
                Slot = slot;
                SourceId = sourceWard;
                SourceItemId = sourceItem;
                Token = token;
                TypeIndex = typeIndex;
                Style = style;
                Rarity = rarity;
                Stall = stall;
                Price = price;
                ItemId = itemId;
                WardId = wardId;
                Asset = asset;
                Name = name;
                TextBase = textBase;
                WardDesc = wardDesc;
                ItemDesc = itemDesc;
            }

            /// <summary>head, face, mouth or back.</summary>
            internal string Slot { get; }

            /// <summary>The donor's wardrobe row (the look is cloned from it).</summary>
            internal long SourceId { get; }

            /// <summary>The donor's bag item (the unlock item and its market row are cloned from it).</summary>
            internal long SourceItemId { get; }

            /// <summary>Lower-case part of the donor's file names that becomes <see cref="Asset"/> (bananahat, hearteyepatch...).</summary>
            internal string Token { get; }

            /// <summary>Key of the wardrobe type list (m_kWardRodeTypeInfo) the look ID is added to: 1 head, 2 face, 3 mouth, 5 back.</summary>
            internal long TypeIndex { get; }

            internal long Style { get; }

            internal long Rarity { get; }

            internal bool Stall { get; }

            internal long Price { get; }

            internal long ItemId { get; }

            internal long WardId { get; }

            /// <summary>Lower-case model name that replaces "bananahat" (e.g. valkyriehat).</summary>
            internal string Asset { get; }

            internal string Name { get; }

            /// <summary>Text IDs: TextBase = name, +1 = wardrobe description, +2 = item description.</summary>
            internal long TextBase { get; }

            internal string WardDesc { get; }

            internal string ItemDesc { get; }

            internal Obf_R.Obf_ff ItemRow { get; set; }

            internal Obf_R.Obf_ff TradeRow { get; set; }

            internal Obf_R.Obf_ff WardRow { get; set; }
        }

        // The custom hats come from <client>\ro3emu\custom\items.tsv, which build_custom_items.py writes from custom-items.csv.
        private static Hat[] Hats = Array.Empty<Hat>();

        private static Hat[] LoadHats()
        {
            var list = new System.Collections.Generic.List<Hat>();
            try
            {
                var path = System.IO.Path.Combine(System.IO.Path.GetDirectoryName(Application.dataPath), "ro3emu", "custom", "items.tsv");
                if (!CustomPack.Exists(path)) { Log("no hat list at " + path + " (run Build-CustomItems.cmd)"); return list.ToArray(); }
                var inv = System.Globalization.CultureInfo.InvariantCulture;
                foreach (var line in CustomPack.ReadAllLines(path, System.Text.Encoding.UTF8))
                {
                    if (line.Length == 0 || line[0] == '#') continue;
                    var c = line.Split('\t');
                    if (c.Length < 12) { Log("bad line in items.tsv: " + line); continue; }
                    // Columns 12-16 (slot, donor look, donor item, donor file token, type list) are missing in an old file: a hat.
                    var slot = c.Length >= 17 ? c[12] : "head";
                    list.Add(new Hat(
                        long.Parse(c[1], inv), long.Parse(c[2], inv), c[4], c[9], long.Parse(c[3], inv), c[10], c[11],
                        long.Parse(c[5], inv), long.Parse(c[6], inv), c[7] == "1", long.Parse(c[8], inv),
                        slot,
                        c.Length >= 17 ? long.Parse(c[13], inv) : 10040434L,
                        c.Length >= 17 ? long.Parse(c[14], inv) : 3004111L,
                        c.Length >= 17 ? c[15] : "bananahat",
                        c.Length >= 17 ? long.Parse(c[16], inv) : 1L));
                }
            }
            catch (Exception e) { Log("hat list: " + e.GetType().Name + ": " + e.Message); }
            return list.ToArray();
        }

        private static readonly string[] Fields =
        {
            "_iAcquire", "_iActionDesc", "_iConflictField", "_iDefaultLightUp", "_iDescription", "_iDisable",
            "_iFashionScore", "_iGiftAward", "_iHeadPos", "_iID", "_iLeaderBoardsID", "_iLuxury", "_iLuxuryGetAction",
            "_iMain", "_iName", "_iQuality", "_iSexNeed", "_iSkillID", "_iSkillVideo", "_iSort", "_iSyntheticMethod",
            "_iSystemID", "_iType", "_iVideo", "_kBackground", "_kBasicAttribute", "_kEffectBonesB", "_kEffectBonesG",
            "_kEffectPathsB", "_kEffectPathsG", "_kIcon", "_kIconB", "_kIconG", "_kIdleAct", "_kIntroAct", "_kMaterial",
            "_kModelResourceB", "_kModelResourceG", "_kSpecialAttribute",
        };

        // Our own texts, answered by the Language.Trans wrapper (the client's text table has no entry for them).
        private static System.Collections.Generic.Dictionary<long, string> Texts = new System.Collections.Generic.Dictionary<long, string>();

        // The ItemConfig row fields (the keys of the exported row of item 3004111).
        private static readonly string[] ItemFields =
        {
            "_QuickUse", "_iAcquire", "_iAuction", "_iAward", "_iBagTab", "_iCD", "_iCanAuto", "_iCanStorehouse", "_iCardGroup",
            "_iCardPart", "_iCustomParameters", "_iDecomposeNotice", "_iDescription", "_iDescription2", "_iDiscard",
            "_iEnchantSlot", "_iEquipId", "_iEquipPart", "_iGroupUse", "_iGroupUseNum", "_iID", "_iIfSameCard", "_iIsEleCard",
            "_iIsHint", "_iIsUse", "_iIsUseNextSeason", "_iLabel", "_iLevelNeed", "_iName", "_iNeedConfirm", "_iPreview",
            "_iPrompt", "_iQuality", "_iRepeatable", "_iSeasonLapseProduct", "_iSeasonResetType", "_iSexNeed", "_iShortcutBar",
            "_iSort", "_iStackLimit", "_iSubType", "_iSubTypeTxt", "_iSynthesisNotice", "_iTimeBoundEnum", "_iTrade", "_iType",
            "_iUseBeforeEffect", "_iUseEffect", "_iUseLimit", "_kAwardperformance", "_kCDGroup", "_kCanCart", "_kCombine",
            "_kCouplingExtraEffect", "_kDecompose", "_kDropFx", "_kDropIcon", "_kDropLight", "_kDropMusic", "_kGamePlayLimit",
            "_kIcon", "_kItemSource", "_kItemTipsBtn", "_kJobNeed", "_kMapLimit", "_kMultiverseArray", "_kParam1", "_kParam2",
            "_kParam3", "_kParam4", "_kParam5", "_kPic", "_kPromptFx", "_kSeriesNeed", "_kSell", "_kTimeBoundPara", "_kUseJump",
            "_kUseLimit", "_kUseTimes",
        };

        private static readonly string[] TradeFields =
        {
            "_iBasePrice", "_iBuyLimit", "_iCopyTriggerLv", "_iCopyTriggerPrice", "_iCopyTriggerStock", "_iInCopyMax", "_iMoney",
            "_iOnePrice", "_iPremiumCard", "_iRecycleTriggerPrice", "_iReduceLv", "_iReduceTigger", "_iScaleDown",
            "_iServerTradeMaxNum", "_iShelveLimit", "_iSort", "_iSubType", "_iTopSalePriceMin", "_iTotalCopyMax", "_iTrade",
            "_iTradeMaxNum", "_iTradeMaxNumLv", "_iTradeStackMaxNum", "_iTradeType", "_iType", "_kGameSeason", "_kIsBindGive",
            "_kMultiverseArray", "_kReduceRange", "_kReduceSpecialTigger", "_kStableRange", "_kVariableRange",
        };

        // Kept alive here: Lua holds the pushed delegates only weakly through xLua's object pool.
        private static readonly TypeLookup LookupWrapper = Lookup;
        private static readonly TransLookup TransWrapper = Trans;
        private static readonly TypeLookup ItemWrapper = ItemEntry;

        private static Obf_R.Obf_ff _global;
        private static Obf_R.Obf_Ef _original, _rawget;
        // Language.Trans: a Lua function, or a C# delegate when the official ConfigOverlay already wrapped it.
        private static object _trans;
        private static object _itemOriginal;
        private static readonly TypeLookup TradeWrapper = TradeEntry;
        private static object _tradeOriginal;
        private static int _tradeFailures;
        private static readonly TypeLookup WardrobeWrapper = WardrobeEntry;
        private static object _wardOriginal;
        private static int _wardCalls, _wardFailures;
        private static int _itemCalls, _itemFailures;
        private static int _transSeen, _transHits;
        private static object _first, _firstItems;
        private static Obf_R.Obf_ff _itemClass;
        private static int _calls;
        private static string _step = "start";

        /// <summary>From LuaPatches.Tick, once per LuaEnv.</summary>
        internal static void Run(Obf_R.Obf_ff global)
        {
            try
            {
                Hats = LoadHats();
                Texts = BuildTexts();
                Log("Run started (build v24, " + Hats.Length + " custom hats)");
                _step = "rawget";
                _global = global;
                _rawget = global.Obf_LuA<Obf_R.Obf_Ef>("rawget");
                _original = null;
                _first = null;
                foreach (var hat in Hats)
                {
                    hat.ItemRow = null;
                    hat.TradeRow = null;
                    hat.WardRow = null;
                }

                _calls = 0;
                _step = "Lua_DBManager";
                var db = global.Obf_LuA<Obf_R.Obf_ff>("Lua_DBManager");
                _step = "GetInstance";
                var instance = db?.Obf_RuA<Obf_R.Obf_Ef>("GetInstance")?.Obf_HuA(db);
                var holder = instance != null && instance.Length > 0 ? instance[0] as Obf_R.Obf_ff : null;
                _step = "m_kCFG_ItemConfig";
                var items = holder?.Obf_RuA<Obf_R.Obf_ff>("m_kCFG_ItemConfig");
                _step = "item rows";
                if (items == null) Log("item config (m_kCFG_ItemConfig) not found");
                else foreach (var hat in Hats) Log("item config instance: " + hat.ItemId + " row added = " + AddItemRow(items, hat));
                if (items != null) { WrapItemEntry(global); SelfTest(items); }
                try
                {
                    // The market (Stall) row: Banana has one in TradeConfig, so the item needs its own copy or the bag says "Untradeable".
                    _step = "trade config";
                    var trade = holder?.Obf_RuA<object>("m_kCFG_TradeConfig") as Obf_R.Obf_ff;
                    if (trade == null) Log("trade config (m_kCFG_TradeConfig) not found yet");
                    else foreach (var hat in Hats) Log("trade config instance: " + hat.ItemId + " row added = " + (hat.Stall && AddTradeRow(trade, hat)));
                    WrapTradeEntry(global);
                }
                catch (Exception e) { Log("trade config: " + e.GetType().Name + ": " + e.Message); }
                _step = "m_kCFG_WardRobeConfig";
                var config = holder?.Obf_RuA<Obf_R.Obf_ff>("m_kCFG_WardRobeConfig");
                _step = "wardrobe rows";
                if (config == null) { Log("config not found, nothing added"); return; }
                _first = config;
                foreach (var hat in Hats) Log("config instance: " + hat.WardId + " row added = " + AddRow(config, hat));
                WrapLookup(global);
                try { WrapWardrobeEntry(global); }
                catch (Exception e) { Log("wardrobe entry wrapper: " + e.GetType().Name + ": " + e.Message); }
                WrapTrans(global);
            }
            catch (Exception e)
            {
                var trace = e.StackTrace ?? string.Empty;
                Debug.LogWarning("[Ro3Emu.WardrobeCustom] " + e.GetType().Name + ": " + e.Message + " | step: " + _step + " | " + (trace.Length > 700 ? trace.Substring(0, 700) : trace));
            }
        }

        /// <summary>Wraps GetWardRodeTypeInfo on the config class (BaseClass's __newindex writes the shared vtbl).</summary>
        private static void WrapLookup(Obf_R.Obf_ff global)
        {
            var require = global.Obf_LuA<Obf_R.Obf_Ef>("require");
            var loaded = global.Obf_LuA<Obf_R.Obf_ff>("package.loaded.lua_cfg_wardrobeconfig");
            var found = loaded ?? (First(require?.Obf_HuA("lua_cfg_wardrobeconfig")) as Obf_R.Obf_ff);
            var original = found?.Obf_RuA<Obf_R.Obf_Ef>("GetWardRodeTypeInfo");
            if (original == null) { Log("config class or GetWardRodeTypeInfo not found, lookup not wrapped"); return; }
            _original = original;
            found.Obf_luA<string, TypeLookup>("GetWardRodeTypeInfo", LookupWrapper);
            Log("GetWardRodeTypeInfo wrapped on the config class");
        }

        /// <summary>Wraps GetEntryPtr on the item config class: whichever object is asked for our item gets the row first.</summary>
        private static void WrapItemEntry(Obf_R.Obf_ff global)
        {
            var require = global.Obf_LuA<Obf_R.Obf_Ef>("require");
            var loaded = global.Obf_LuA<Obf_R.Obf_ff>("package.loaded.lua_cfg_itemconfig");
            var found = loaded ?? (First(require?.Obf_HuA("lua_cfg_itemconfig")) as Obf_R.Obf_ff);
            var original = found?.Obf_RuA<object>("GetEntryPtr");
            if (original == null) { Log("item config class or GetEntryPtr not found, item lookup not wrapped"); return; }
            _itemOriginal = original;
            _itemClass = found;
            found.Obf_luA<string, TypeLookup>("GetEntryPtr", ItemWrapper);
            Log("GetEntryPtr wrapped on the item config class");
        }

        private static object ItemEntry(object self, object id)
        {
            try
            {
                if (self is Obf_R.Obf_ff table && FindByItem(LuaPatches.ToLong(id)) is Hat hat)
                {
                    bool added = AddItemRow(table, hat);
                    bool same = ReferenceEquals(self, _firstItems);
                    if (_itemCalls++ < MaxCallLogs || added)
                        Log("GetEntryPtr(" + hat.ItemId + ") on " + (same ? "the first" : "ANOTHER") + " item config object; row added now = " + added);
                }
            }
            catch (Exception e)
            {
                if (_itemFailures++ < 6)
                {
                    var trace = e.StackTrace ?? string.Empty;
                    Log("item lookup wrapper: " + e.GetType().Name + ": " + e.Message + " | " + (trace.Length > 500 ? trace.Substring(0, 500) : trace));
                }
            }
            return CallEntry(_itemOriginal, self, id);
        }

        /// <summary>Asks the item config what it answers for our item (GetName, GetIcon, GetEntryPtr) and logs it.</summary>
        private static void SelfTest(Obf_R.Obf_ff items)
        {
            _firstItems = items;
            if (Hats.Length > 0) Compare(items, Hats[0]);
            foreach (var hat in Hats)
            {
                foreach (var method in new[] { "GetName", "GetIcon", "GetType", "GetQuality" })
                {
                    try
                    {
                        var answer = First(items.Obf_RuA<Obf_R.Obf_Ef>(method)?.Obf_HuA(items, hat.ItemId));
                        Log("item self-test " + method + "(" + hat.ItemId + ") = " + (answer == null ? "nil" : "'" + answer + "' (" + answer.GetType().Name + ")"));
                    }
                    catch (Exception e) { Log("item self-test " + method + ": " + e.GetType().Name + ": " + e.Message); }
                }
            }
        }

        /// <summary>Logs the methods of the item config class and every Get... answer that differs between Banana's item and ours.</summary>
        private static void Compare(Obf_R.Obf_ff items, Hat hat)
        {
            var cls = _itemClass;
            var next = _global.Obf_LuA<Obf_R.Obf_Ef>("next");
            if (cls == null || next == null) { Log("compare: no class or next"); return; }
            var names = new System.Collections.Generic.List<string>();
            Collect(next, cls, names);
            if (cls.Obf_RuA<object>("__index") is Obf_R.Obf_ff index && !ReferenceEquals(index, cls)) Collect(next, index, names);
            Log("item config class methods (" + names.Count + "): " + string.Join(",", names));
            var tried = 0;
            foreach (var name in names)
            {
                if (!name.StartsWith("Get", StringComparison.Ordinal) || tried >= 90) continue;
                tried++;
                try
                {
                    var method = items.Obf_RuA<Obf_R.Obf_Ef>(name);
                    var banana = Show(First(method?.Obf_HuA(items, hat.SourceItemId)));
                    var ours = Show(First(method?.Obf_HuA(items, hat.ItemId)));
                    if (banana != ours) Log("  differs " + name + ": Banana=" + banana + " ours=" + ours);
                }
                catch (Exception e) { Log("  " + name + ": " + e.GetType().Name); }
            }
        }

        private static void Collect(Obf_R.Obf_Ef next, Obf_R.Obf_ff table, System.Collections.Generic.List<string> names)
        {
            object key = null;
            for (var i = 0; i < 600; i++)
            {
                var pair = next.Obf_HuA(table, key);
                if (pair == null || pair.Length < 2 || pair[0] == null) break;
                key = pair[0];
                if (key is string name && pair[1] is Obf_R.Obf_Ef) names.Add(name);
            }
        }

        private static bool _diagnosed;

        /// <summary>Once: logs every key of the item config object and of its class (name:type) to see what this client's table really holds.</summary>
        private static void Diagnose(Obf_R.Obf_ff config)
        {
            if (_diagnosed) return;
            _diagnosed = true;
            try
            {
                Dump("item config object", config);
                var meta = _global.Obf_LuA<Obf_R.Obf_Ef>("getmetatable")?.Obf_HuA(config);
                var metaTable = meta != null && meta.Length > 0 ? meta[0] as Obf_R.Obf_ff : null;
                if (metaTable == null) { Log("item config object has no metatable"); return; }
                Dump("item config metatable", metaTable);
                if (metaTable.Obf_RuA<object>("__index") is Obf_R.Obf_ff index) Dump("item config class (__index)", index);
                else
                {
                    var indexFn = metaTable.Obf_RuA<Obf_R.Obf_Ef>("__index");
                    var viaFn = indexFn?.Obf_HuA(config, "GetEntryPtr");
                    var viaValue = viaFn != null && viaFn.Length > 0 ? viaFn[0] : null;
                    Log("__index function answers GetEntryPtr with: " + (viaValue == null ? "nil" : viaValue.GetType().FullName));
                }

                try
                {
                    var direct = config.Obf_RuA<object>("GetEntryPtr");
                    Log("config.GetEntryPtr read as object: " + (direct == null ? "nil" : direct.GetType().FullName));
                }
                catch (Exception e) { Log("config.GetEntryPtr read as object: " + e.GetType().Name + ": " + e.Message); }
                var loaded = _global.Obf_LuA<Obf_R.Obf_ff>("package.loaded.lua_cfg_itemconfig");
                if (loaded == null) Log("package.loaded.lua_cfg_itemconfig: nil");
                else Dump("package.loaded.lua_cfg_itemconfig", loaded);
            }
            catch (Exception e) { Log("diagnose: " + e.GetType().Name + ": " + e.Message); }
        }

        private static void Dump(string label, Obf_R.Obf_ff table)
        {
            var next = _global.Obf_LuA<Obf_R.Obf_Ef>("next");
            var text = new System.Text.StringBuilder();
            object key = null;
            for (var i = 0; i < 150; i++)
            {
                var pair = next.Obf_HuA(table, key);
                if (pair == null || pair.Length < 2 || pair[0] == null) break;
                key = pair[0];
                text.Append(key).Append(':').Append(pair[1] == null ? "nil" : pair[1].GetType().Name).Append(' ');
            }

            Log(label + ": " + text);
        }

        private static string Show(object value) =>
            value == null ? "nil" : value is string text ? "'" + text + "'" : value is Obf_R.Obf_ff ? "table" : value is Obf_R.Obf_Ef ? "function" : value.ToString();

        /// <summary>Wraps Language.Trans so our own text IDs answer with our own strings.</summary>
        private static void WrapTrans(Obf_R.Obf_ff global)
        {
            var language = global.Obf_LuA<Obf_R.Obf_ff>("Language");
            var original = language?.Obf_RuA<object>("Trans");
            if (original == null) { Log("Language.Trans not found, own names will be empty"); return; }
            _trans = original;
            language.Obf_luA<string, TransLookup>("Trans", TransWrapper);
            Log("Language.Trans wrapped");
        }

        private static object Trans(object key, object param)
        {
            try
            {
                var id = key is Obf_R.Obf_ff table ? LuaPatches.ToLong(First(_rawget?.Obf_HuA(table, 1L))) : LuaPatches.ToLong(key);
                if (_transSeen++ == 0) Log("Trans wrapper first call, key " + (key == null ? "null" : key.GetType().Name));
                if (Texts.TryGetValue(id, out var text))
                {
                    if (_transHits++ < 4) Log("Trans answered id " + id + " -> " + text);
                    return text;
                }
            }
            catch (Exception e) { Log("Trans wrapper: " + e.GetType().Name + ": " + e.Message); }
            return CallEntry(_trans, key, param);
        }

        private static object Lookup(object self, object type)
        {
            try
            {
                if (self is Obf_R.Obf_ff table)
                {
                    bool same = ReferenceEquals(self, _first);
                    var added = false;
                    foreach (var hat in Hats) added |= AddRow(table, hat);
                    if (_calls++ < MaxCallLogs || added)
                        Log("GetWardRodeTypeInfo(" + LuaPatches.ToLong(type) + ") on " + (same ? "the first" : "ANOTHER") + " config object; row added now = " + added);
                }
            }
            catch (Exception e) { Log("lookup wrapper: " + e.GetType().Name + ": " + e.Message); }
            return First(_original?.Obf_HuA(self, type));
        }

        /// <summary>Adds the test row to this config object and its headgear ID list; false when it was already there.</summary>
        private static bool AddRow(Obf_R.Obf_ff config, Hat hat)
        {
            var global = _global;
            var rawget = global.Obf_LuA<Obf_R.Obf_Ef>("rawget");
            var rawset = global.Obf_LuA<Obf_R.Obf_Ef>("rawset");
            var pack = global.Obf_LuA<Obf_R.Obf_Ef>("table.pack");
            // "as", not a cast: another config object may hold these as something else, and then there is nothing to add to.
            var values = (config.Obf_RuA<object>("m_kConfigData") as Obf_R.Obf_ff)?.Obf_RuA<object>("m_kValues") as Obf_R.Obf_ff;
            if (rawget == null || rawset == null || pack == null || values == null) return false;
            if (First(rawget.Obf_HuA(values, hat.WardId)) != null) return false;
            // Built once; every other wardrobe config object just gets the same table.
            hat.WardRow ??= BuildWardRow(config, pack, rawset, hat);
            if (hat.WardRow == null) return false;
            rawset.Obf_HuA(values, hat.WardId, hat.WardRow);
            rawset.Obf_HuA(config, "m_kAllIDList", null);
            var info = config.Obf_RuA<Obf_R.Obf_ff>("m_kWardRodeTypeInfo");
            var list = info == null ? null : First(rawget.Obf_HuA(info, hat.TypeIndex)) as Obf_R.Obf_ff;
            var length = list == null ? 0 : LuaPatches.ToLong(First(global.Obf_LuA<Obf_R.Obf_Ef>("rawlen")?.Obf_HuA(list)));
            // The ID list may be one table shared by several config objects: add the ID only once.
            var present = false;
            for (var i = 1L; i <= length && !present; i++) present = LuaPatches.ToLong(First(rawget.Obf_HuA(list, i))) == hat.WardId;
            if (length > 0 && !present) rawset.Obf_HuA(list, length + 1, hat.WardId);
            return true;
        }

        private static Obf_R.Obf_ff BuildWardRow(Obf_R.Obf_ff config, Obf_R.Obf_Ef pack, Obf_R.Obf_Ef rawset, Hat hat)
        {
            var source = new[] { CallEntry(config.Obf_RuA<object>("GetEntryPtr"), config, hat.SourceId) };
            var src = First(source) as Obf_R.Obf_ff;
            if (src == null) return null;
            var row = (Obf_R.Obf_ff)pack.Obf_HuA()[0];
            rawset.Obf_HuA(row, "n", null);
            rawset.Obf_HuA(row, "__paramslen", null);
            foreach (var field in Fields) rawset.Obf_HuA(row, field, src.Obf_RuA<object>(field));
            // Own icon and model: every name that points to the Banana Hat becomes this hat's own (CustomAssets serves them).
            foreach (var field in Fields)
                if (src.Obf_RuA<object>(field) is string text && text.Contains(hat.Token, StringComparison.OrdinalIgnoreCase))
                {
                    var changed = text.Replace(hat.Token, hat.Asset, StringComparison.OrdinalIgnoreCase);
                    rawset.Obf_HuA(row, field, changed);
                    Log("field " + field + ": " + text + " -> " + changed);
                }
            rawset.Obf_HuA(row, "_iID", hat.WardId);
            rawset.Obf_HuA(row, "_iSort", 1L);
            rawset.Obf_HuA(row, "_iFashionScore", hat.Style);
            rawset.Obf_HuA(row, "_iQuality", hat.Rarity);
            rawset.Obf_HuA(row, "_iName", TextKey(pack, rawset, hat.TextBase));
            rawset.Obf_HuA(row, "_iDescription", TextKey(pack, rawset, hat.TextBase + 1));
            return row;
        }

        /// <summary>Wraps GetEntryPtr on the wardrobe config class: whichever object is asked for our look (avatar, character select, wardrobe) gets the row first.</summary>
        private static void WrapWardrobeEntry(Obf_R.Obf_ff global)
        {
            var require = global.Obf_LuA<Obf_R.Obf_Ef>("require");
            var loaded = global.Obf_LuA<Obf_R.Obf_ff>("package.loaded.lua_cfg_wardrobeconfig");
            var found = loaded ?? (First(require?.Obf_HuA("lua_cfg_wardrobeconfig")) as Obf_R.Obf_ff);
            var original = found?.Obf_RuA<object>("GetEntryPtr");
            if (original == null) { Log("wardrobe config class or GetEntryPtr not found, avatar lookup not wrapped"); return; }
            _wardOriginal = original;
            found.Obf_luA<string, TypeLookup>("GetEntryPtr", WardrobeWrapper);
            Log("GetEntryPtr wrapped on the wardrobe config class");
        }

        private static object WardrobeEntry(object self, object id)
        {
            try
            {
                if (self is Obf_R.Obf_ff table && FindByWard(LuaPatches.ToLong(id)) is Hat hat)
                {
                    bool added = AddRow(table, hat);
                    if (_wardCalls++ < MaxCallLogs || added)
                        Log("GetEntryPtr(" + hat.WardId + ") on a wardrobe config object; ours = " + ReferenceEquals(self, _first) + ", row added now = " + added);
                }
            }
            catch (Exception e)
            {
                if (_wardFailures++ < 4)
                {
                    var trace = e.StackTrace ?? string.Empty;
                    Log("wardrobe lookup wrapper: " + e.GetType().Name + ": " + e.Message + " | " + (trace.Length > 400 ? trace.Substring(0, 400) : trace));
                }
            }
            return CallEntry(_wardOriginal, self, id);
        }

        /// <summary>
        /// Adds the item that unlocks the look (a copy of Banana Hat item 3004111 with its own name, icon and use effect
        /// [[200, 0, wardrobe ID]]) to the client's ItemConfig; false when it was already there or could not be built.
        /// </summary>
        private static bool AddItemRow(Obf_R.Obf_ff config, Hat hat)
        {
            var global = _global;
            var rawget = global.Obf_LuA<Obf_R.Obf_Ef>("rawget");
            var rawset = global.Obf_LuA<Obf_R.Obf_Ef>("rawset");
            var pack = global.Obf_LuA<Obf_R.Obf_Ef>("table.pack");
            // "as", not a cast: another config object may hold these as something else, and then there is nothing to add to.
            var values = (config.Obf_RuA<object>("m_kConfigData") as Obf_R.Obf_ff)?.Obf_RuA<object>("m_kValues") as Obf_R.Obf_ff;
            if (rawget == null || rawset == null || pack == null || values == null) return false;
            if (First(rawget.Obf_HuA(values, hat.ItemId)) != null) return false;
            Diagnose(config);
            if (hat.ItemRow != null)
            {
                // The row was built once; any other item config object just gets the same table.
                rawset.Obf_HuA(values, hat.ItemId, hat.ItemRow);
                rawset.Obf_HuA(config, "m_kAllIDList", null);
                Log("item " + hat.ItemId + " row shared into another item config object");
                return true;
            }
            var src = CallEntry(config.Obf_RuA<object>("GetEntryPtr"), config, hat.SourceItemId) as Obf_R.Obf_ff;
            if (src == null) { Log("item " + hat.SourceItemId + " not found in the client's ItemConfig"); return false; }
            var row = (Obf_R.Obf_ff)pack.Obf_HuA()[0];
            rawset.Obf_HuA(row, "n", null);
            rawset.Obf_HuA(row, "__paramslen", null);
            foreach (var field in ItemFields) rawset.Obf_HuA(row, field, src.Obf_RuA<object>(field));
            // Use or Stall, never Dismantle: _iTrade stays as Banana's (market, needs a TradeConfig row) and no dismantle payout.
            var noDecompose = (Obf_R.Obf_ff)pack.Obf_HuA()[0];
            rawset.Obf_HuA(noDecompose, "n", null);
            rawset.Obf_HuA(noDecompose, "__paramslen", null);
            rawset.Obf_HuA(row, "_kDecompose", noDecompose);
            rawset.Obf_HuA(row, "_iID", hat.ItemId);
            rawset.Obf_HuA(row, "_iQuality", hat.Rarity);
            if (!hat.Stall) rawset.Obf_HuA(row, "_iTrade", 0L);
            rawset.Obf_HuA(row, "_iSort", hat.ItemId);
            rawset.Obf_HuA(row, "_iName", TextKey(pack, rawset, hat.TextBase));
            rawset.Obf_HuA(row, "_iDescription", TextKey(pack, rawset, hat.TextBase + 2));
            rawset.Obf_HuA(row, "_kIcon", "item_" + hat.ItemId + ".png");
            // _iUseEffect: a new {{200, 0, wardrobe ID}} with the number types of the original entry.
            var effects = src.Obf_RuA<object>("_iUseEffect") as Obf_R.Obf_ff;
            var first = effects == null ? null : First(rawget.Obf_HuA(effects, 1L)) as Obf_R.Obf_ff;
            if (first == null) { Log("item " + hat.SourceItemId + " has no _iUseEffect entry"); return false; }
            var inner = (Obf_R.Obf_ff)pack.Obf_HuA()[0];
            rawset.Obf_HuA(inner, "n", null);
            rawset.Obf_HuA(inner, "__paramslen", null);
            var third = First(rawget.Obf_HuA(first, 3L));
            for (var i = 1L; i <= 2; i++) rawset.Obf_HuA(inner, i, First(rawget.Obf_HuA(first, i)));
            rawset.Obf_HuA(inner, 3L, third is string ? hat.WardId.ToString(System.Globalization.CultureInfo.InvariantCulture) : (object)hat.WardId);
            var outer = (Obf_R.Obf_ff)pack.Obf_HuA()[0];
            rawset.Obf_HuA(outer, "n", null);
            rawset.Obf_HuA(outer, "__paramslen", null);
            rawset.Obf_HuA(outer, 1L, inner);
            rawset.Obf_HuA(row, "_iUseEffect", outer);
            rawset.Obf_HuA(values, hat.ItemId, row);
            rawset.Obf_HuA(config, "m_kAllIDList", null);
            hat.ItemRow = row;
            Log("item " + hat.ItemId + " added (use effect 200 -> wardrobe " + hat.WardId + ", _iIsUse copied, tradeable, no dismantle; from " + hat.SourceItemId + ")");
            return true;
        }

        /// <summary>The market row of the item (a copy of Banana Hat's TradeConfig row with our ID and name); false when it was already there or could not be built.</summary>
        private static bool AddTradeRow(Obf_R.Obf_ff config, Hat hat)
        {
            var global = _global;
            var rawget = global.Obf_LuA<Obf_R.Obf_Ef>("rawget");
            var rawset = global.Obf_LuA<Obf_R.Obf_Ef>("rawset");
            var pack = global.Obf_LuA<Obf_R.Obf_Ef>("table.pack");
            var values = (config.Obf_RuA<object>("m_kConfigData") as Obf_R.Obf_ff)?.Obf_RuA<object>("m_kValues") as Obf_R.Obf_ff;
            if (rawget == null || rawset == null || pack == null || values == null) return false;
            if (First(rawget.Obf_HuA(values, hat.ItemId)) != null) return false;
            if (hat.TradeRow == null)
            {
                var src = CallEntry(config.Obf_RuA<object>("GetEntryPtr"), config, hat.SourceItemId) as Obf_R.Obf_ff;
                if (src == null) { Log("market row " + hat.SourceItemId + " not found in the client's TradeConfig"); return false; }
                var row = (Obf_R.Obf_ff)pack.Obf_HuA()[0];
                rawset.Obf_HuA(row, "n", null);
                rawset.Obf_HuA(row, "__paramslen", null);
                foreach (var field in TradeFields) rawset.Obf_HuA(row, field, src.Obf_RuA<object>(field));
                rawset.Obf_HuA(row, "_iID", hat.ItemId);
                rawset.Obf_HuA(row, "_kSpuName", hat.Name);
                rawset.Obf_HuA(row, "_iBasePrice", hat.Price);
                hat.TradeRow = row;
            }
            rawset.Obf_HuA(values, hat.ItemId, hat.TradeRow);
            rawset.Obf_HuA(config, "m_kAllIDList", null);
            Log("market row " + hat.ItemId + " added (copy of " + hat.SourceItemId + ")");
            return true;
        }

        /// <summary>Wraps GetEntryPtr on the trade config class: whichever object is asked for our item gets the market row first.</summary>
        private static void WrapTradeEntry(Obf_R.Obf_ff global)
        {
            var require = global.Obf_LuA<Obf_R.Obf_Ef>("require");
            var loaded = global.Obf_LuA<Obf_R.Obf_ff>("package.loaded.lua_cfg_tradeconfig");
            var found = loaded ?? (First(require?.Obf_HuA("lua_cfg_tradeconfig")) as Obf_R.Obf_ff);
            var original = found?.Obf_RuA<object>("GetEntryPtr");
            if (original == null) { Log("trade config class or GetEntryPtr not found, market lookup not wrapped"); return; }
            _tradeOriginal = original;
            found.Obf_luA<string, TypeLookup>("GetEntryPtr", TradeWrapper);
            Log("GetEntryPtr wrapped on the trade config class");
        }

        private static object TradeEntry(object self, object id)
        {
            try
            {
                if (self is Obf_R.Obf_ff table && FindByItem(LuaPatches.ToLong(id)) is Hat hat) AddTradeRow(table, hat);
            }
            catch (Exception e)
            {
                if (_tradeFailures++ < 4)
                {
                    var trace = e.StackTrace ?? string.Empty;
                    Log("market lookup wrapper: " + e.GetType().Name + ": " + e.Message + " | " + (trace.Length > 400 ? trace.Substring(0, 400) : trace));
                }
            }
            return CallEntry(_tradeOriginal, self, id);
        }

        /// <summary>A text key as the tables hold it: the Lua array {id}.</summary>
        private static Obf_R.Obf_ff TextKey(Obf_R.Obf_Ef pack, Obf_R.Obf_Ef rawset, long id)
        {
            var key = (Obf_R.Obf_ff)pack.Obf_HuA()[0];
            rawset.Obf_HuA(key, "n", null);
            rawset.Obf_HuA(key, "__paramslen", null);
            rawset.Obf_HuA(key, 1L, id);
            return key;
        }

        private static System.Collections.Generic.Dictionary<long, string> BuildTexts()
        {
            var texts = new System.Collections.Generic.Dictionary<long, string>();
            foreach (var hat in Hats)
            {
                texts[hat.TextBase] = hat.Name;
                texts[hat.TextBase + 1] = hat.WardDesc;
                texts[hat.TextBase + 2] = hat.ItemDesc;
            }

            return texts;
        }

        private static Hat FindByItem(long itemId)
        {
            foreach (var hat in Hats)
                if (hat.ItemId == itemId) return hat;
            return null;
        }

        private static Hat FindByWard(long wardId)
        {
            foreach (var hat in Hats)
                if (hat.WardId == wardId) return hat;
            return null;
        }

        /// <summary>Calls a config's GetEntryPtr whatever it is: a Lua function, or a C# delegate another hook (ConfigOverlay) put there.</summary>
        private static object CallEntry(object function, object self, object id) =>
            function is Obf_R.Obf_Ef lua ? First(lua.Obf_HuA(self, id)) : function is Delegate native ? native.DynamicInvoke(self, id) : null;

        private static object First(object[] result) => result != null && result.Length > 0 ? result[0] : null;

        private static void Log(string message) => Debug.Log("[Ro3Emu.WardrobeCustom] " + message);
    }
}
