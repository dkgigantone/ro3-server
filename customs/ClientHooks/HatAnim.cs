using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using UnityEngine;

namespace Ro3Emu.ClientHooks
{
    /// <summary>
    /// ANIMATED custom hats (prototype). A hat that has a folder <c>ro3emu\hat-anim\&lt;Key&gt;\*.bundle</c> (the Origin
    /// bundles that belong to the hat, carved by import_hat.py) is not shown as the static OBJ: the Origin prefab (mesh,
    /// skeleton, Animator, clips) is loaded from the bundles, instantiated under the worn hat object, given the game's own
    /// material with our texture, and placed with the same <c>hats\&lt;Name&gt;.txt</c> settings the OBJ route uses.
    /// If anything fails, <see cref="TryApply"/> returns false and <see cref="HatSwap"/> falls back to the static OBJ.
    /// Everything is logged ("[Ro3Emu.HatAnim]") because Origin is built with Unity 2019.4 and this client with 2022.3.
    /// </summary>
    internal static class HatAnim
    {
        private const string InstanceName = "Ro3Emu_AnimHat";
        private const int MaxLogs = 160;
        private static readonly string[] TextureProps = { "_MainTex", "_BaseMap", "_BaseColorMap" };
        private static readonly Dictionary<string, Set> Sets = new Dictionary<string, Set>(StringComparer.Ordinal);
        private static readonly Dictionary<int, Pose> Poses = new Dictionary<int, Pose>();
        private static readonly Dictionary<string, Tex> Textures = new Dictionary<string, Tex>(StringComparer.Ordinal);
        private static int _logs;
        private static int _errors;

        private sealed class Set
        {
            public GameObject Prefab;
        }

        /// <summary>How the instance was placed: the mesh holder's pose inside the prefab, the settings in use, the materials made.</summary>
        private sealed class Pose
        {
            public Quaternion Rot = Quaternion.identity;
            public Vector3 Scale = Vector3.one;
            public long TxtStamp = -1;
            public long PngStamp = -1;
            public Material[] Materials = Array.Empty<Material>();
        }

        private sealed class Tex
        {
            public long Stamp;
            public Texture2D Texture;
        }

        private static string BaseKey(string hat) =>
            hat.Length > 1 && (hat.EndsWith('M') || hat.EndsWith('F')) ? hat.Substring(0, hat.Length - 1) : hat;

        private static string Dir(string baseKey) =>
            Path.Combine(Path.GetDirectoryName(Application.dataPath) ?? "", Path.Combine("ro3emu", Path.Combine("hat-anim", baseKey)));

        private static void Log(string message)
        {
            if (_logs++ < MaxLogs) Debug.Log("[Ro3Emu.HatAnim] " + message);
        }

        /// <summary>True when the animated hat is in place for this worn hat object (the static OBJ is then not used).</summary>
        internal static bool TryApply(MeshFilter filter, string hatsDir, string hat)
        {
            var key = BaseKey(hat);
            if (!Sets.TryGetValue(key, out var set))
            {
                var dir = Dir(key);
                set = Directory.Exists(dir) ? LoadSet(key, dir) : new Set();
                Sets[key] = set;
            }
            if (set.Prefab == null) return false;
            try
            {
                Place(filter, hatsDir, hat, set);
                return true;
            }
            catch (Exception e)
            {
                if (_errors++ < 20) Debug.LogError("[Ro3Emu.HatAnim] " + hat + ": " + e);
                return false;
            }
        }

        /// <summary>The worn object is not (or no longer) one of our hats: remove a leftover animated instance and show the game's mesh again.</summary>
        internal static void Release(MeshFilter filter)
        {
            var child = filter.transform.Find(InstanceName);
            if (child == null) return;
            Poses.Remove(child.gameObject.GetInstanceID());
            UnityEngine.Object.Destroy(child.gameObject);
            var renderer = filter.GetComponent<MeshRenderer>();
            if (renderer != null) renderer.enabled = true;
        }

        private static Set LoadSet(string key, string dir)
        {
            var set = new Set();
            try
            {
                var files = Directory.GetFiles(dir, "*.bundle");
                Array.Sort(files, StringComparer.Ordinal);
                var bundles = new List<AssetBundle>();
                foreach (var file in files)
                {
                    var bundle = AssetBundle.LoadFromFile(file);
                    if (bundle == null)
                    {
                        Log(key + ": could not load " + Path.GetFileName(file));
                        continue;
                    }
                    bundles.Add(bundle);
                    var names = bundle.GetAllAssetNames();
                    Log(key + ": " + Path.GetFileName(file) + " holds " + names.Length + " assets: " + Join(names, 12));
                }
                GameObject best = null;
                var bestScore = 0;
                foreach (var bundle in bundles)
                {
                    foreach (var name in bundle.GetAllAssetNames())
                    {
                        if (!name.EndsWith(".prefab", StringComparison.OrdinalIgnoreCase) && !name.EndsWith(".fbx", StringComparison.OrdinalIgnoreCase)) continue;
                        var go = bundle.LoadAsset<GameObject>(name);
                        if (go == null) continue;
                        var renderers = go.GetComponentsInChildren<Renderer>(true).Length;
                        var animators = go.GetComponentsInChildren<Animator>(true).Length;
                        Log(key + ": candidate " + name + " renderers=" + renderers + " animators=" + animators);
                        var score = (renderers * 10) + animators;
                        if (score > bestScore)
                        {
                            best = go;
                            bestScore = score;
                        }
                    }
                    foreach (var clip in bundle.LoadAllAssets<AnimationClip>())
                        Log(key + ": clip " + clip.name + " length=" + clip.length + " looping=" + clip.isLooping + " legacy=" + clip.legacy);
                }
                if (best == null)
                {
                    Log(key + ": no prefab with a renderer was found - using the static OBJ");
                    return set;
                }
                var sb = new StringBuilder();
                Dump(best.transform, 0, sb, new int[1]);
                Log(key + ": chosen " + best.name + "\n" + sb);
                set.Prefab = best;
            }
            catch (Exception e)
            {
                Debug.LogError("[Ro3Emu.HatAnim] " + key + ": load failed: " + e);
            }
            return set;
        }

        private static void Place(MeshFilter filter, string hatsDir, string hat, Set set)
        {
            var donor = filter.GetComponent<MeshRenderer>();
            if (donor != null && donor.enabled) donor.enabled = false;
            var holder = filter.transform;
            var child = holder.Find(InstanceName);
            Pose pose;
            if (child == null)
            {
                var inst = UnityEngine.Object.Instantiate(set.Prefab);
                inst.name = InstanceName;
                inst.SetActive(true);
                pose = MakePose(inst);
                inst.transform.SetParent(holder, false);
                SetLayer(inst.transform, filter.gameObject.layer);
                Poses[inst.GetInstanceID()] = pose;
                StartAnimators(inst);
                child = inst.transform;
                Log(hat + ": animated instance created under " + holder.name);
            }
            else if (!Poses.TryGetValue(child.gameObject.GetInstanceID(), out pose))
            {
                pose = new Pose();
                Poses[child.gameObject.GetInstanceID()] = pose;
            }
            var txt = Path.Combine(hatsDir, hat + ".txt");
            var png = Path.Combine(hatsDir, hat + ".png");
            var txtStamp = Stamp(txt);
            var pngStamp = Stamp(png);
            if (pose.PngStamp != pngStamp || pose.Materials.Length == 0)
            {
                ApplyMaterials(child.gameObject, donor, png, pose);
                pose.PngStamp = pngStamp;
            }
            if (pose.TxtStamp != txtStamp)
            {
                ApplyTransform(child, HatOptions.Load(txt), pose);
                pose.TxtStamp = txtStamp;
            }
        }

        /// <summary>The pose of the main renderer inside the prefab as imported (so the .txt settings mean the same as for the OBJ).</summary>
        private static Pose MakePose(GameObject inst)
        {
            var pose = new Pose();
            Renderer main = inst.GetComponentInChildren<SkinnedMeshRenderer>(true);
            if (main == null) main = inst.GetComponentInChildren<MeshRenderer>(true);
            if (main == null) return pose;
            var root = inst.transform;
            pose.Rot = Quaternion.Inverse(root.rotation) * main.transform.rotation;
            var a = main.transform.lossyScale;
            var b = root.lossyScale;
            pose.Scale = new Vector3(Safe(a.x) / Safe(b.x), Safe(a.y) / Safe(b.y), Safe(a.z) / Safe(b.z));
            Log("main renderer " + main.name + " rotation inside prefab " + pose.Rot.eulerAngles + " scale " + pose.Scale);
            return pose;
        }

        private static float Safe(float v) => Mathf.Abs(v) < 1e-5f ? 1f : v;

        private static void ApplyTransform(Transform t, HatOptions o, Pose pose)
        {
            var s = o.Scale;
            t.localRotation = Quaternion.Euler(o.RotX, o.RotY, o.RotZ) * Quaternion.Inverse(pose.Rot);
            t.localScale = new Vector3((o.MirrorX ? -s : s) / Safe(pose.Scale.x), s / Safe(pose.Scale.y), s / Safe(pose.Scale.z));
            t.localPosition = new Vector3(o.OffX, o.OffY, o.OffZ);
        }

        /// <summary>Every renderer gets a copy of the game's own hat material (the Origin shaders are for another Unity version) with our texture.</summary>
        private static void ApplyMaterials(GameObject inst, MeshRenderer donor, string png, Pose pose)
        {
            var template = donor != null ? donor.sharedMaterial : null;
            var texture = GetTexture(png);
            var created = new List<Material>();
            foreach (var r in inst.GetComponentsInChildren<Renderer>(true))
            {
                if (template == null) continue;
                var count = Mathf.Max(1, r.sharedMaterials.Length);
                var mats = new Material[count];
                for (var i = 0; i < count; i++)
                {
                    mats[i] = new Material(template);
                    created.Add(mats[i]);
                }
                r.sharedMaterials = mats;
            }
            foreach (var m in created)
            {
                if (texture == null) continue;
                foreach (var prop in TextureProps)
                    if (m.HasProperty(prop)) m.SetTexture(prop, texture);
            }
            foreach (var old in pose.Materials)
                if (old != null) UnityEngine.Object.Destroy(old);
            pose.Materials = created.ToArray();
            if (pose.Materials.Length == 0) pose.Materials = new Material[1];
        }

        private static Texture2D GetTexture(string png)
        {
            var stamp = Stamp(png);
            if (!Textures.TryGetValue(png, out var tex))
            {
                tex = new Tex();
                Textures[png] = tex;
            }
            if (tex.Texture != null && tex.Stamp == stamp) return tex.Texture;
            if (tex.Texture != null) UnityEngine.Object.Destroy(tex.Texture);
            tex.Texture = null;
            tex.Stamp = stamp;
            if (!CustomPack.Exists(png)) return null;
            tex.Texture = new Texture2D(2, 2, TextureFormat.RGBA32, true);
            tex.Texture.LoadImage(CustomPack.ReadAllBytes(png));
            return tex.Texture;
        }

        private static void StartAnimators(GameObject inst)
        {
            foreach (var a in inst.GetComponentsInChildren<Animator>(true))
            {
                a.enabled = true;
                a.cullingMode = AnimatorCullingMode.AlwaysAnimate;
                var controller = a.runtimeAnimatorController;
                Log("animator on " + a.name + ": controller " + (controller == null ? "NONE" : controller.name + " clips=" + controller.animationClips.Length) + ", avatar " + (a.avatar == null ? "none" : a.avatar.name));
                a.Rebind();
                a.Update(0f);
            }
            foreach (var anim in inst.GetComponentsInChildren<Animation>(true))
                Log("legacy Animation on " + anim.name + ", clips=" + anim.GetClipCount() + ", playAutomatically=" + anim.playAutomatically);
        }

        private static void SetLayer(Transform t, int layer)
        {
            t.gameObject.layer = layer;
            for (var i = 0; i < t.childCount; i++) SetLayer(t.GetChild(i), layer);
        }

        private static void Dump(Transform t, int depth, StringBuilder sb, int[] count)
        {
            if (count[0]++ >= 40) return;
            sb.Append(' ', depth * 2).Append(t.name).Append("  pos ").Append(t.localPosition).Append(" rot ").Append(t.localEulerAngles).Append(" scale ").Append(t.localScale).Append("  [");
            foreach (var c in t.GetComponents<Component>()) sb.Append(c == null ? "missing" : c.GetType().Name).Append(' ');
            sb.Append("]\n");
            for (var i = 0; i < t.childCount; i++) Dump(t.GetChild(i), depth + 1, sb, count);
        }

        private static string Join(string[] names, int max)
        {
            var sb = new StringBuilder();
            for (var i = 0; i < names.Length && i < max; i++) sb.Append(i > 0 ? " | " : "").Append(names[i]);
            if (names.Length > max) sb.Append(" ...");
            return sb.ToString();
        }

        private static long Stamp(string file) => CustomPack.Stamp(file);
    }
}
