using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using UnityEngine;

namespace Ro3Emu.ClientHooks
{
    /// <summary>
    /// Own assets for custom items. The patcher inserts a call to <see cref="TryLoad"/> at the entry of
    /// AssetLoader.LoadAssetByPriority (HappyEngine.Resource.dll). A path is custom when its file name has
    /// <list type="bullet">
    /// <item>a PNG in <c>&lt;client&gt;\ro3emu\custom\icons</c>: answered with an AssetElement of our own whose Asset is a
    /// Sprite built from that PNG (wardrobe icons);</item>
    /// <item>an alias file <c>&lt;name&gt;.alias</c> in <c>&lt;client&gt;\ro3emu\custom\models</c> (line 1: the original asset
    /// to copy, line 2: <c>from=to</c> rename of the objects' names): answered with an AssetElement of our own whose Asset is
    /// a hidden, renamed copy of that original prefab (hat models; HatSwap then gives the renamed copy its own mesh).</item>
    /// </list>
    /// Every other path returns null and the client's loader runs unchanged. Our elements carry the hash of their own path
    /// (the prefab pool keys on it) and are not registered in the loader's cache, so an original asset is never shared with
    /// or touched by a custom one. The AssetElement API is used through reflection (this DLL does not reference
    /// HappyEngine.Resource).
    /// </summary>
    public static class CustomAssets
    {
        private const BindingFlags Any = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance;
        private const int MaxLogs = 60;

        private static readonly Dictionary<string, object> Elements = new Dictionary<string, object>(StringComparer.Ordinal);
        private static readonly HashSet<string> Missing = new HashSet<string>(StringComparer.Ordinal);
        private static int _logs;
        private static bool _loggedDir;

        private static Type _elementType;
        private static MethodInfo _setAsset, _getAsset, _setPath, _setHash, _addRef, _hashFunction, _isDone, _addLoadCompleted, _doLoadCompleted;
        private static FieldInfo _isError;

        private static string Root() => Path.Combine(Path.GetDirectoryName(Application.dataPath) ?? "", Path.Combine("ro3emu", "custom"));

        internal static string IconDir() => Path.Combine(Root(), "icons");

        internal static string ModelDir() => Path.Combine(Root(), "models");

        /// <summary>Returns an AssetElement for a custom path, or null to let the original method run.</summary>
        public static object TryLoad(object loader, string path, int priority, object callback)
        {
            try
            {
                if (string.IsNullOrEmpty(path) || loader == null) return null;
                var name = Path.GetFileName(path).ToLowerInvariant();
                if (Missing.Contains(name)) return null;
                if (Elements.TryGetValue(name, out var known)) return Reuse(loader, known, callback);
                if (!_loggedDir) { _loggedDir = true; Log("custom folder " + Root()); }
                object element = null;
                var alias = Path.Combine(ModelDir(), name + ".alias");
                var icon = Path.Combine(IconDir(), name);
                var model = File.Exists(alias);
                if (model) element = BuildModel(loader, path, priority, callback, alias);
                else if (name.EndsWith(".png", StringComparison.Ordinal) && File.Exists(icon)) element = BuildIcon(loader, path, icon);
                if (element == null) { Missing.Add(name); return null; }
                Elements[name] = element;
                if (!model && callback != null) Deliver(loader, element, callback); // a model's callback is queued by BuildModel
                return element;
            }
            catch (Exception e)
            {
                Log("TryLoad " + path + ": " + e.GetType().Name + ": " + e.Message);
                return null;
            }
        }

        /// <summary>
        /// DOTSPathResolver.ResolvePath(path, canUseDots) hook (Render.dll): the client switches "x.prefab" to "x_dots.prefab"
        /// only when its resource loader knows the _dots asset. For a custom model (alias file) it is switched anyway;
        /// null means "not ours, run the original".
        /// </summary>
        public static string ResolveDots(string path)
        {
            try
            {
                if (string.IsNullOrEmpty(path) || !path.EndsWith(".prefab", StringComparison.Ordinal) || path.Contains("_dots.prefab")) return null;
                var dots = path.Replace(".prefab", "_dots.prefab");
                var name = Path.GetFileName(dots).ToLowerInvariant();
                return File.Exists(Path.Combine(ModelDir(), name + ".alias")) ? dots : null;
            }
            catch (Exception)
            {
                return null;
            }
        }

        private static object Reuse(object loader, object element, object callback)
        {
            _addRef.Invoke(element, null);
            if (callback == null) return element;
            if ((bool)_isDone.Invoke(element, null)) Deliver(loader, element, callback);
            else _addLoadCompleted.Invoke(element, new[] { callback });
            return element;
        }

        private static object NewElement(object loader, string path)
        {
            Bind(loader);
            var element = Activator.CreateInstance(_elementType);
            _setPath.Invoke(element, new object[] { path });
            if (_hashFunction != null && _setHash != null) _setHash.Invoke(element, new[] { _hashFunction.Invoke(null, new object[] { path }) });
            _addRef.Invoke(element, null); // the caller's reference
            _addRef.Invoke(element, null); // pinned for good: the count never reaches zero, so it is never recycled
            return element;
        }

        private static object BuildIcon(object loader, string path, string file)
        {
            var texture = new Texture2D(2, 2, TextureFormat.RGBA32, false) { name = Path.GetFileName(file) };
            if (!ImageConversion.LoadImage(texture, File.ReadAllBytes(file), true)) { Log("cannot decode " + file); return null; }
            var sprite = Sprite.Create(texture, new Rect(0f, 0f, texture.width, texture.height), new Vector2(0.5f, 0.5f), 100f);
            sprite.name = texture.name;
            var element = NewElement(loader, path);
            _setAsset.Invoke(element, new object[] { sprite });
            Log("custom icon " + path + " <- " + file + " (" + texture.width + "x" + texture.height + ")");
            return element;
        }

        /// <summary>The element starts empty (not done); it is completed when the original prefab has loaded.</summary>
        private static object BuildModel(object loader, string path, int priority, object callback, string aliasFile)
        {
            var lines = File.ReadAllLines(aliasFile);
            // Line 1: the original asset to copy. Line 2 (optional): "from=to", the rename for a prefab copy; without it the
            // original's loaded asset is shared as is (meshes and textures).
            var split = lines.Length < 2 || lines[1].Trim().Length == 0 ? new string[0] : lines[1].Trim().Split('=');
            if (lines.Length < 1 || lines[0].Trim().Length == 0 || (split.Length != 0 && (split.Length != 2 || split[0].Length == 0))) { Log(aliasFile + ": expected the original asset, then optionally from=to"); return null; }
            var directory = Path.GetDirectoryName(path) ?? "";
            var source = string.IsNullOrEmpty(directory) ? lines[0].Trim() : directory + "/" + lines[0].Trim();
            var element = NewElement(loader, path);
            if (callback != null) _addLoadCompleted.Invoke(element, new[] { callback });
            var request = new ModelRequest { Element = element, AssetPath = path, From = split.Length == 2 ? split[0] : null, To = split.Length == 2 ? split[1] : null };
            var loadType = loader.GetType();
            var method = loadType.GetMethod("LoadAssetByPriority", Any);
            var action = method.GetParameters()[2].ParameterType;
            var done = Delegate.CreateDelegate(action, request, typeof(ModelRequest).GetMethod("Loaded"));
            Log("custom model " + path + " <- copy of " + source + (split.Length == 2 ? " (rename " + split[0] + " -> " + split[1] + ")" : " (shared as is)"));
            method.Invoke(loader, new object[] { source, priority, done });
            return element;
        }

        private sealed class ModelRequest
        {
            public object Element;
            public string AssetPath;
            public string From;
            public string To;

            /// <summary>The original prefab's AssetElement (null on failure); never released, so its bundle stays loaded.</summary>
            public void Loaded(object source)
            {
                try
                {
                    var original = source == null ? null : _getAsset.Invoke(source, null);
                    var prefab = original as GameObject;
                    if (original != null && From == null)
                    {
                        _setAsset.Invoke(Element, new[] { original });
                        Log("custom asset " + AssetPath + ": shares the original's " + original.GetType().Name);
                    }
                    else if (prefab == null)
                    {
                        _isError.SetValue(Element, true);
                        Log("custom model " + AssetPath + ": the original prefab did not load");
                    }
                    else
                    {
                        var template = UnityEngine.Object.Instantiate(prefab);
                        template.SetActive(false); // instances are activated by the pool (SetActive(DefaultActiveValue))
                        UnityEngine.Object.DontDestroyOnLoad(template);
                        var renamed = 0;
                        foreach (var t in template.GetComponentsInChildren<Transform>(true))
                        {
                            var changed = t.gameObject.name.Replace(From, To, StringComparison.OrdinalIgnoreCase);
                            if (changed == t.gameObject.name) continue;
                            t.gameObject.name = changed;
                            renamed++;
                        }
                        _setAsset.Invoke(Element, new object[] { template });
                        Log("custom model " + AssetPath + ": ready, root '" + template.name + "', " + renamed + " object(s) renamed");
                    }
                }
                catch (Exception e)
                {
                    _isError.SetValue(Element, true);
                    Log("custom model " + AssetPath + ": " + e.GetType().Name + ": " + e.Message);
                }
                _doLoadCompleted.Invoke(Element, null);
            }
        }

        private static void Bind(object loader)
        {
            if (_elementType != null) return;
            var assembly = loader.GetType().Assembly;
            var type = assembly.GetType("HappyEngine.Runtime.NewRuntimeSystem.AssetElement", true);
            _setAsset = type.GetProperty("Asset", Any).GetSetMethod(true);
            _getAsset = type.GetProperty("Asset", Any).GetGetMethod(true);
            _setPath = type.GetProperty("AssetPath", Any).GetSetMethod(true);
            _setHash = type.GetProperty("AssetHash", Any)?.GetSetMethod(true);
            _addRef = type.GetMethod("AddRef", Any);
            _isDone = type.GetProperty("IsDone", Any).GetGetMethod(true);
            _addLoadCompleted = type.GetMethod("AddLoadCompleted", Any);
            _doLoadCompleted = type.GetMethod("DoLoadCompleted", Any);
            _isError = type.GetField("_isError", Any);
            _hashFunction = assembly.GetType("HappyEngine.Runtime.NewRuntimeSystem.MurmurHash")?.GetMethod("GetLowerHashCode", BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static);
            _elementType = type;
        }

        /// <summary>Queues the callback like the loader does for an element that is already loaded.</summary>
        private static void Deliver(object loader, object element, object callback)
        {
            try
            {
                var field = loader.GetType().GetField("_delayedCallbacks", Any);
                if (field?.GetValue(loader) is IList list)
                {
                    var infoType = field.FieldType.GetGenericArguments()[0];
                    object info = Activator.CreateInstance(infoType);
                    infoType.GetField("Element", Any).SetValue(info, element);
                    infoType.GetField("Callback", Any).SetValue(info, callback);
                    list.Add(info);
                    return;
                }
            }
            catch (Exception e)
            {
                Log("delayed callback: " + e.GetType().Name + ": " + e.Message);
            }
            ((Delegate)callback).DynamicInvoke(element);
        }

        private static void Log(string message)
        {
            if (_logs++ < MaxLogs) Debug.Log("[Ro3Emu.CustomAssets] " + message);
        }
    }
}
