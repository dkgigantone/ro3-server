using System;
using System.Collections.Generic;
using System.Reflection;
using UnityEngine;

namespace Ro3Emu.ClientHooks
{
    /// <summary>
    /// Read-only probe for custom item assets: the client's resource patch calls these at the start of
    /// PrefabPoolManager.TryGetInstanceFromPool (model name) and ResourceInfo.GetSprite (icon name). Each returns the
    /// name unchanged; the first distinct headgear/wardrobe-related names are written to the log once, so we can see
    /// the exact strings the game asks for.
    /// </summary>
    public static class ResourceProbe
    {
        private const int MaxLogs = 150;
        private static readonly string[] Keywords = { "headaccs", "wardrobe_icon", "valkyrie", "bananahat" };
        private static readonly HashSet<string> Seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        private static readonly object Gate = new object();
        private static int _failures;
        private static PropertyInfo _pathProperty;

        /// <summary>The model (prefab) name the client is about to spawn; returned unchanged.</summary>
        public static string OnPrefab(string name)
        {
            Note("prefab", name);
            return name;
        }

        /// <summary>The icon (sprite) name the client is about to load; returned unchanged.</summary>
        public static string OnSprite(string name)
        {
            Note("sprite", name);
            return name;
        }

        /// <summary>The icon name given to AssetInfo.GetSprite; returned unchanged.</summary>
        public static string OnAssetSprite(string name)
        {
            Note("assetinfo-sprite", name);
            return name;
        }

        /// <summary>The path given to BundleLoader.LoadAssetAsync; returned unchanged.</summary>
        public static string OnBundleLoad(string path)
        {
            Note("bundle-load", path);
            return path;
        }

        /// <summary>The path given to AssetElement.LoadAssetAsync; returned unchanged.</summary>
        public static string OnElementLoad(string path)
        {
            Note("element-load", path);
            return path;
        }

        /// <summary>The atlas name given to ResourceManifest.TryGetAtlas; returned unchanged.</summary>
        public static string OnAtlas(string name)
        {
            Note("atlas", name);
            return name;
        }

        /// <summary>
        /// AssetElement.set_Asset(value): an asset finished loading. Logs the requested path and the runtime type of the
        /// loaded object for Banana/Valkyrie assets (to learn what an icon or a model load really returns); returns the
        /// value unchanged.
        /// </summary>
        public static object OnAssetSet(object element, object value)
        {
            try
            {
                var path = PathOf(element);
                if (path != null && (path.Contains("bananahat", StringComparison.OrdinalIgnoreCase) || path.Contains("valkyrie", StringComparison.OrdinalIgnoreCase)))
                    Note("asset-set", path + " -> " + (value == null ? "null" : value.GetType().FullName));
            }
            catch (Exception e)
            {
                if (_failures++ == 0) Debug.LogWarning("[Ro3Emu.ResourceProbe] " + e.GetType().Name + ": " + e.Message);
            }
            return value;
        }

        private static string PathOf(object element)
        {
            if (element == null) return null;
            if (_pathProperty == null) _pathProperty = element.GetType().GetProperty("AssetPath");
            return _pathProperty == null ? null : _pathProperty.GetValue(element, null) as string;
        }

        private static void Note(string kind, string name)
        {
            try
            {
                if (string.IsNullOrEmpty(name) || !Interesting(name)) return;
                lock (Gate)
                {
                    if (Seen.Count >= MaxLogs || !Seen.Add(kind + ":" + name)) return;
                }
                Debug.Log("[Ro3Emu.ResourceProbe] " + kind + " " + name);
            }
            catch (Exception e)
            {
                if (_failures++ == 0) Debug.LogWarning("[Ro3Emu.ResourceProbe] " + e.GetType().Name + ": " + e.Message);
            }
        }

        private static bool Interesting(string name)
        {
            foreach (var keyword in Keywords)
                if (name.Contains(keyword, StringComparison.OrdinalIgnoreCase)) return true;
            return false;
        }
    }
}
