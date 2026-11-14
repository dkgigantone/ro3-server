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
    /// <item>a bundle file <c>&lt;name&gt;.bundle</c> in the same folder (name = the requested file name, with a trailing
    /// <c>_dots</c> before <c>.prefab</c> ignored): a Unity AssetBundle built in the same Unity version as the client. The
    /// first prefab inside it becomes the Asset (skinned job bodies built in the Unity editor).</item>
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
                if (BodyProbe.Wants(name) && !CustomPack.Exists(Path.Combine(ModelDir(), name + ".rgm")) && !CustomPack.Exists(Path.Combine(ModelDir(), name))) { var probed = ProbeLoad(loader, path, priority, callback, name); if (probed != null) return probed; }
                if (Elements.TryGetValue(name, out var known)) return Reuse(loader, known, callback);
                if (!_loggedDir) { _loggedDir = true; Log("custom folder " + Root()); }
                object element = null;
                var alias = Path.Combine(ModelDir(), name + ".alias");
                var icon = Path.Combine(IconDir(), name);
                var model = CustomPack.Exists(alias);
                var bundleFile = Path.Combine(ModelDir(), name.Replace("_dots.prefab", ".prefab") + ".bundle");
                if (model) element = BuildModel(loader, path, priority, callback, alias);
                else if (name.EndsWith(".prefab", StringComparison.Ordinal) && CustomPack.Exists(bundleFile)) element = BuildBundle(loader, path, bundleFile);
                else if (name.StartsWith("model_body_", StringComparison.Ordinal) && name.EndsWith(".asset", StringComparison.Ordinal) && CustomPack.Exists(Path.Combine(ModelDir(), name + ".rgm"))) element = BuildBodyMesh(loader, path, Path.Combine(ModelDir(), name + ".rgm"));
                else if (name.StartsWith("model_body_", StringComparison.Ordinal) && name.EndsWith(".png", StringComparison.Ordinal) && CustomPack.Exists(Path.Combine(ModelDir(), name))) element = BuildBodyTexture(loader, path, Path.Combine(ModelDir(), name));
                else if (name.EndsWith(".png", StringComparison.Ordinal) && CustomPack.Exists(icon)) element = BuildIcon(loader, path, icon);
                if (element == null)
                {
                    if (name.StartsWith("model_body_", StringComparison.Ordinal)) Log("body asset requested (not custom): " + path);
                    Missing.Add(name);
                    return null;
                }
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
                return CustomPack.Exists(Path.Combine(ModelDir(), name + ".alias")) ? dots : null;
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
            if (!ImageConversion.LoadImage(texture, CustomPack.ReadAllBytes(file), true)) { Log("cannot decode " + file); return null; }
            var sprite = Sprite.Create(texture, new Rect(0f, 0f, texture.width, texture.height), new Vector2(0.5f, 0.5f), 100f);
            sprite.name = texture.name;
            var element = NewElement(loader, path);
            _setAsset.Invoke(element, new object[] { sprite });
            Log("custom icon " + path + " <- " + file + " (" + texture.width + "x" + texture.height + ")");
            return element;
        }

        /// <summary>Diagnostic only (probe-body.txt): the original loader runs unchanged; our callback also sees the loaded asset.</summary>
        private static object ProbeLoad(object loader, string path, int priority, object callback, string name)
        {
            BodyProbe.Mark(name);
            Bind(loader);
            var method = loader.GetType().GetMethod("LoadAssetByPriority", Any);
            var action = method.GetParameters()[2].ParameterType;
            var request = new ProbeRequest { Name = name };
            var done = Delegate.CreateDelegate(action, request, typeof(ProbeRequest).GetMethod("Loaded"));
            var element = method.Invoke(loader, new object[] { path, priority, done });
            if (element == null) return null;
            _addRef.Invoke(element, null); // the caller's own reference (ours was taken by the nested call)
            if (callback != null)
            {
                if ((bool)_isDone.Invoke(element, null)) Deliver(loader, element, callback);
                else _addLoadCompleted.Invoke(element, new[] { callback });
            }
            return element;
        }

        private sealed class ProbeRequest
        {
            public string Name;

            public void Loaded(object source)
            {
                try { BodyProbe.Dump(Name, source == null ? null : _getAsset.Invoke(source, null)); }
                catch (Exception e) { Log("probe " + Name + ": " + e.GetType().Name + ": " + e.Message); }
            }
        }

        /// <summary>A converted job body (.rgm, see claude/rogue-hskf-skeleton-findings.md): a Mesh in the game's own 3-stream layout with the official skeleton's bind poses.</summary>
        private static object BuildBodyMesh(object loader, string path, string file)
        {
            var data = CustomPack.ReadAllBytes(file);
            if (data.Length < 40 || data[0] != (byte)'R' || data[1] != (byte)'G' || data[2] != (byte)'M' || data[3] != (byte)'1') { Log("bad body mesh " + file); return null; }
            var n = BitConverter.ToInt32(data, 4);
            var nIdx = BitConverter.ToInt32(data, 8);
            var nBones = BitConverter.ToInt32(data, 12);
            var p = 16;
            var b = new float[6];
            for (var i = 0; i < 6; i++, p += 4) b[i] = BitConverter.ToSingle(data, p);
            var mesh = new Mesh { name = Path.GetFileNameWithoutExtension(file) };
            mesh.SetVertexBufferParams(n,
                new UnityEngine.Rendering.VertexAttributeDescriptor(UnityEngine.Rendering.VertexAttribute.Position, UnityEngine.Rendering.VertexAttributeFormat.Float32, 3, 0),
                new UnityEngine.Rendering.VertexAttributeDescriptor(UnityEngine.Rendering.VertexAttribute.Normal, UnityEngine.Rendering.VertexAttributeFormat.Float32, 3, 0),
                new UnityEngine.Rendering.VertexAttributeDescriptor(UnityEngine.Rendering.VertexAttribute.Tangent, UnityEngine.Rendering.VertexAttributeFormat.Float32, 4, 0),
                new UnityEngine.Rendering.VertexAttributeDescriptor(UnityEngine.Rendering.VertexAttribute.Color, UnityEngine.Rendering.VertexAttributeFormat.UNorm8, 4, 1),
                new UnityEngine.Rendering.VertexAttributeDescriptor(UnityEngine.Rendering.VertexAttribute.TexCoord0, UnityEngine.Rendering.VertexAttributeFormat.Float32, 2, 1),
                new UnityEngine.Rendering.VertexAttributeDescriptor(UnityEngine.Rendering.VertexAttribute.TexCoord1, UnityEngine.Rendering.VertexAttributeFormat.Float32, 2, 1),
                new UnityEngine.Rendering.VertexAttributeDescriptor(UnityEngine.Rendering.VertexAttribute.BlendWeight, UnityEngine.Rendering.VertexAttributeFormat.Float32, 4, 2),
                new UnityEngine.Rendering.VertexAttributeDescriptor(UnityEngine.Rendering.VertexAttribute.BlendIndices, UnityEngine.Rendering.VertexAttributeFormat.UInt32, 4, 2));
            var s0 = new byte[n * 40]; Buffer.BlockCopy(data, p, s0, 0, s0.Length); p += s0.Length;
            var s1 = new byte[n * 20]; Buffer.BlockCopy(data, p, s1, 0, s1.Length); p += s1.Length;
            var s2 = new byte[n * 32]; Buffer.BlockCopy(data, p, s2, 0, s2.Length); p += s2.Length;
            mesh.SetVertexBufferData(s0, 0, 0, s0.Length, 0);
            mesh.SetVertexBufferData(s1, 0, 0, s1.Length, 1);
            mesh.SetVertexBufferData(s2, 0, 0, s2.Length, 2);
            var idx = new ushort[nIdx]; Buffer.BlockCopy(data, p, idx, 0, nIdx * 2); p += nIdx * 2;
            mesh.SetIndexBufferParams(nIdx, UnityEngine.Rendering.IndexFormat.UInt16);
            mesh.SetIndexBufferData(idx, 0, 0, nIdx);
            mesh.subMeshCount = 1;
            mesh.SetSubMesh(0, new UnityEngine.Rendering.SubMeshDescriptor(0, nIdx, MeshTopology.Triangles) { bounds = new Bounds(new Vector3(b[0], b[1], b[2]), new Vector3(b[3] * 2f, b[4] * 2f, b[5] * 2f)), vertexCount = n });
            mesh.bounds = new Bounds(new Vector3(b[0], b[1], b[2]), new Vector3(b[3] * 2f, b[4] * 2f, b[5] * 2f));
            var poses = new Matrix4x4[nBones];
            for (var i = 0; i < nBones; i++) for (var r = 0; r < 4; r++) for (var c = 0; c < 4; c++, p += 4) poses[i][r, c] = BitConverter.ToSingle(data, p);
            mesh.bindposes = poses;
            mesh.UploadMeshData(true);
            var element = NewElement(loader, path);
            _setAsset.Invoke(element, new object[] { mesh });
            Log("custom body mesh " + path + " <- " + file + " (" + n + " verts, file written " + (File.Exists(file) ? File.GetLastWriteTime(file).ToString("HH:mm:ss") : "in pack") + ")");
            return element;
        }

        private static object BuildBodyTexture(object loader, string path, string file)
        {
            var texture = new Texture2D(2, 2, TextureFormat.RGBA32, true) { name = Path.GetFileName(file) };
            if (!ImageConversion.LoadImage(texture, CustomPack.ReadAllBytes(file), false)) { Log("cannot decode " + file); return null; }
            var element = NewElement(loader, path);
            _setAsset.Invoke(element, new object[] { texture });
            Log("custom body texture " + path + " <- " + file + " (" + texture.width + "x" + texture.height + ")");
            return element;
        }

        private static readonly Dictionary<string, GameObject> Bundles = new Dictionary<string, GameObject>(StringComparer.Ordinal);

        /// <summary>Loads the bundle from memory (loose file or pack) and answers with its first prefab; never unloaded.</summary>
        private static object BuildBundle(object loader, string path, string file)
        {
            if (!Bundles.TryGetValue(file, out var prefab))
            {
                var bundle = AssetBundle.LoadFromMemory(CustomPack.ReadAllBytes(file));
                if (bundle == null) { Log("cannot load bundle " + file); return null; }
                foreach (var assetName in bundle.GetAllAssetNames())
                {
                    if (!assetName.EndsWith(".prefab", StringComparison.OrdinalIgnoreCase)) continue;
                    prefab = bundle.LoadAsset<GameObject>(assetName);
                    if (prefab != null) break;
                }
                if (prefab == null) { Log("bundle " + file + " has no prefab"); bundle.Unload(true); return null; }
                Bundles[file] = prefab;
            }
            var element = NewElement(loader, path);
            _setAsset.Invoke(element, new object[] { prefab });
            Log("custom bundle " + path + " <- " + file + " (prefab '" + prefab.name + "')");
            return element;
        }

        /// <summary>The element starts empty (not done); it is completed when the original prefab has loaded.</summary>
        private static object BuildModel(object loader, string path, int priority, object callback, string aliasFile)
        {
            var lines = CustomPack.ReadAllLines(aliasFile);
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
