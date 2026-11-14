using System;
using UnityEngine;

namespace Ro3Emu.ClientHooks
{
    /// <summary>
    /// Keeps the custom hats up to date while you tune them in the hat studio (a separate Windows panel, tools\HatStudio.ps1,
    /// that writes <c>ro3emu\hats\&lt;Name&gt;.txt</c>): one host object whose Update calls <see cref="HatSwap.Poll"/> every
    /// other frame. Created once from <see cref="UiTweaks.Tick"/>.
    /// </summary>
    public static class HatStudio
    {
        private static GameObject _host;
        private static bool _failed;

        internal static void Tick()
        {
            if (_host != null || _failed) return;
            try
            {
                _host = new GameObject("Ro3Emu.HatStudio");
                UnityEngine.Object.DontDestroyOnLoad(_host);
                _host.AddComponent<HatStudioHost>();
            }
            catch (Exception e)
            {
                _failed = true;
                Debug.LogError("[Ro3Emu.HatStudio] " + e);
            }
        }
    }

    /// <summary>The per-frame host; public because Unity adds it as a component.</summary>
    public sealed class HatStudioHost : MonoBehaviour
    {
        private int _frame;

        private void Update()
        {
            if (++_frame % 2 == 0) HatSwap.Poll();
        }
    }
}
