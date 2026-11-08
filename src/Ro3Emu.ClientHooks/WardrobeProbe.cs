using System;
using UnityEngine;

namespace Ro3Emu.ClientHooks
{
    /// <summary>
    /// Read-only probe for the custom-headgear work: logs how the client's WardRobeConfig row of the Banana Hat
    /// (10040434) looks in the live Lua state, so a new row can later be built the same way. Changes nothing.
    /// Looks the config up two ways (the module class and Lua_DBManager's instance) and logs which one has data.
    /// </summary>
    internal static class WardrobeProbe
    {
        private const long BananaHat = 10040434;

        private static readonly string[] Fields =
        {
            "_iID", "_iType", "_iQuality", "_iName", "_iDescription", "_kIconB", "_kIconG",
            "_kModelResourceB", "_kModelResourceG", "_iConflictField", "_kMaterial",
        };

        /// <summary>From LuaPatches.Tick, once per LuaEnv.</summary>
        internal static void Run(LA_LuaManager manager, Obf_R.Obf_ff global)
        {
            try
            {
                Describe("module class", LuaPatches.Module(manager, global, "lua_cfg_wardrobeconfig"));
                var db = global.Obf_LuA<Obf_R.Obf_ff>("Lua_DBManager");
                var instance = db?.Obf_RuA<Obf_R.Obf_Ef>("GetInstance")?.Obf_HuA(db);
                var holder = instance != null && instance.Length > 0 ? instance[0] as Obf_R.Obf_ff : null;
                Describe("DBManager instance", holder?.Obf_RuA<Obf_R.Obf_ff>("m_kCFG_WardRobeConfig"));
            }
            catch (Exception e)
            {
                Debug.LogWarning("[Ro3Emu.WardrobeProbe] " + e.GetType().Name + ": " + e.Message);
            }
        }

        private static void Describe(string label, Obf_R.Obf_ff config)
        {
            if (config == null) { Debug.Log("[Ro3Emu.WardrobeProbe] " + label + ": not found"); return; }
            var data = config.Obf_RuA<object>("m_kConfigData");
            Debug.Log("[Ro3Emu.WardrobeProbe] " + label + ": m_kConfigData is " + (data == null ? "nil" : data.GetType().Name));
            var found = config.Obf_RuA<Obf_R.Obf_Ef>("GetEntryPtr")?.Obf_HuA(config, BananaHat);
            var row = found != null && found.Length > 0 ? found[0] : null;
            Debug.Log("[Ro3Emu.WardrobeProbe] " + label + ": row " + BananaHat + " is " + (row == null ? "nil" : row.GetType().Name));
            if (!(row is Obf_R.Obf_ff table)) return;
            foreach (var field in Fields)
            {
                var value = table.Obf_RuA<object>(field);
                Debug.Log("[Ro3Emu.WardrobeProbe]   " + field + " = " + (value == null ? "nil" : value.GetType().Name + " " + value));
            }
        }
    }
}
