using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using UnityEngine;

namespace Ro3Emu.ClientHooks
{
    /// <summary>
    /// Reskins a worn headgear with a model from another game (private, local use). For every file
    /// <c>ro3emu\hats\&lt;Name&gt;.obj</c> (+ <c>&lt;Name&gt;.png</c> texture and <c>&lt;Name&gt;.txt</c> settings, see
    /// <see cref="HatOptions"/>), any worn hat object <c>Model_HeadAccs_&lt;Name&gt;_dots(Clone)</c> gets that mesh and
    /// texture. Hats without a file are untouched. The game pools hat objects and puts its own mesh back, so the hats found
    /// by <see cref="Tick"/> are re-checked every other frame by <see cref="Poll"/>: the OBJ is parsed once, and a change to
    /// the settings only rebuilds the vertices, so edits made in the hat studio show within a frame or two.
    /// </summary>
    public static class HatSwap
    {
        private const string Prefix = "Model_HeadAccs_";
        private static readonly string[] TextureProps = { "_MainTex", "_BaseMap", "_BaseColorMap" };
        private static readonly Dictionary<string, Loaded> Cache = new Dictionary<string, Loaded>(StringComparer.Ordinal);
        private static readonly List<Tracked> TrackedHats = new List<Tracked>();
        private static int _logged;
        private static int _errors;
        private static int _frame;

        internal static string HatDir() => Path.Combine(Path.GetDirectoryName(Application.dataPath) ?? "", Path.Combine("ro3emu", "hats"));

        /// <summary>From UiTweaks.Tick: every third pass, finds the worn hats that have a file.</summary>
        public static void Tick()
        {
            if (++_frame % 3 != 0) return;
            var dir = HatDir();
            if (!Directory.Exists(dir)) return;
            TrackedHats.Clear();
            foreach (var filter in UnityEngine.Object.FindObjectsOfType<MeshFilter>())
            {
                var name = filter.gameObject.name;
                if (!name.StartsWith(Prefix, StringComparison.Ordinal)) continue;
                var hat = name.Substring(Prefix.Length);
                var end = hat.IndexOf("_dots", StringComparison.Ordinal);
                if (end > 0) hat = hat.Substring(0, end);
                if (File.Exists(Path.Combine(dir, hat + ".obj"))) TrackedHats.Add(new Tracked { Filter = filter, Hat = hat });
                else HatAnim.Release(filter);
            }
            Poll();
        }

        /// <summary>Called every frame from UiTweaks.Tick: swings the ribbon vertices of hats with sway=1, once per frame per hat; the mesh is shared by all worn copies.</summary>
        internal static void Animate()
        {
            if (TrackedHats.Count == 0) return;
            try
            {
                SwayRibbons();
            }
            catch (Exception e)
            {
                if (_errors++ < 20) Debug.LogError("[Ro3Emu.HatSwap] sway: " + e);
            }
        }

        private static void SwayRibbons()
        {
            var frame = Time.frameCount;
            foreach (var tracked in TrackedHats)
            {
                if (tracked.Filter == null || !Cache.TryGetValue(tracked.Hat, out var l)) continue;
                if (!l.Sway || l.Mesh == null || l.RibbonVertex == null || tracked.Filter.sharedMesh != l.Mesh || l.AnimatedFrame == frame) continue;
                l.AnimatedFrame = frame;
                var t = Time.time * l.SwaySpeed;
                for (var k = 0; k < l.RibbonVertex.Length; k++)
                {
                    var w = l.Weight[k];
                    var a = l.SwayAmp * w * Mathf.Sin(t + l.Phase[k]);
                    var b = l.SwayAmp * 0.4f * w * Mathf.Sin(t * 1.3f + l.Phase[k] * 0.7f + 1f);
                    var q = Quaternion.AngleAxis(a, l.AxisFront) * Quaternion.AngleAxis(b, l.AxisSide);
                    var i = l.RibbonVertex[k];
                    l.Current[i] = l.Hinge[k] + q * (l.Base[i] - l.Hinge[k]);
                }
                l.Mesh.vertices = l.Current;
                l.Mesh.RecalculateBounds();
            }
        }

        /// <summary>Applies the files to the tracked hats; cheap when nothing changed (a few file timestamps).</summary>
        internal static void Poll()
        {
            if (TrackedHats.Count == 0) return;
            var dir = HatDir();
            foreach (var tracked in TrackedHats)
                if (tracked.Filter != null) Apply(tracked.Filter, dir, tracked.Hat);
        }

        private sealed class Tracked
        {
            public MeshFilter Filter;
            public string Hat;
        }

        /// <summary>A hat's parsed OBJ, texture and the mesh built from them with the current settings.</summary>
        private sealed class Loaded
        {
            public long ObjStamp;
            public long TxtStamp;
            public long PngStamp;
            public Vector3[] Pos;
            public Vector2[] Uv;
            public int[] Tris;
            public Mesh Mesh;
            public Texture2D Texture;
            // Ribbon sway (only when the settings say sway=1): final-space base positions, hinge points and weights.
            public bool Sway;
            public float SwayAmp;
            public float SwaySpeed;
            public Vector3[] Base;
            public Vector3[] Current;
            public int[] RibbonVertex;
            public Vector3[] Hinge;
            public float[] Weight;
            public float[] Phase;
            public Vector3 AxisFront;
            public Vector3 AxisSide;
            public int AnimatedFrame = -1;
        }

        private static long Stamp(string file) => File.Exists(file) ? File.GetLastWriteTimeUtc(file).Ticks : 0;

        private static Loaded Load(string dir, string hat)
        {
            var obj = Path.Combine(dir, hat + ".obj");
            var txt = Path.Combine(dir, hat + ".txt");
            var png = Path.Combine(dir, hat + ".png");
            if (!Cache.TryGetValue(hat, out var loaded))
            {
                loaded = new Loaded();
                Cache[hat] = loaded;
            }
            var objStamp = Stamp(obj);
            var txtStamp = Stamp(txt);
            var pngStamp = Stamp(png);
            var rebuild = loaded.Mesh == null || loaded.TxtStamp != txtStamp;
            if (loaded.Pos == null || loaded.ObjStamp != objStamp)
            {
                ParseObj(File.ReadAllLines(obj), loaded);
                loaded.ObjStamp = objStamp;
                rebuild = true;
            }
            if (loaded.PngStamp != pngStamp)
            {
                ReloadTexture(loaded, png);
                loaded.PngStamp = pngStamp;
                rebuild = true;
            }
            if (!rebuild) return loaded;
            var options = HatOptions.Load(txt);
            if (loaded.Mesh != null) UnityEngine.Object.Destroy(loaded.Mesh);
            loaded.Mesh = Build(loaded, options);
            loaded.Mesh.MarkDynamic();
            loaded.Mesh.name = "Ro3Emu_" + hat;
            loaded.TxtStamp = txtStamp;
            return loaded;
        }

        private static void ReloadTexture(Loaded loaded, string png)
        {
            if (loaded.Texture != null) UnityEngine.Object.Destroy(loaded.Texture);
            loaded.Texture = null;
            if (!File.Exists(png)) return;
            loaded.Texture = new Texture2D(2, 2, TextureFormat.RGBA32, true);
            loaded.Texture.LoadImage(File.ReadAllBytes(png));
        }

        private static void Apply(MeshFilter filter, string dir, string hat)
        {
            try
            {
                if (HatAnim.TryApply(filter, dir, hat)) return;
                var loaded = Load(dir, hat);
                if (filter.sharedMesh == loaded.Mesh) return;
                filter.sharedMesh = loaded.Mesh;
                var renderer = filter.GetComponent<MeshRenderer>();
                if (renderer != null && loaded.Texture != null) SetTexture(renderer, loaded.Texture);
                if (_logged++ < 40) Debug.Log("[Ro3Emu.HatSwap] " + hat + " applied, bounds " + loaded.Mesh.bounds.size);
            }
            catch (IOException)
            {
                // The studio is writing the file; the next pass reads it.
            }
            catch (Exception e)
            {
                if (_errors++ < 20) Debug.LogError("[Ro3Emu.HatSwap] " + hat + ": " + e);
            }
        }

        private static void SetTexture(MeshRenderer renderer, Texture2D texture)
        {
            var material = renderer.material; // a per-renderer instance
            foreach (var prop in TextureProps)
                if (material.HasProperty(prop)) material.SetTexture(prop, texture);
        }

        /// <summary>Wavefront OBJ (v / vt / f, polygons fan-triangulated) into one vertex per v/vt pair.</summary>
        private static void ParseObj(string[] lines, Loaded target)
        {
            var positions = new List<Vector3>();
            var uvs = new List<Vector2>();
            var outPos = new List<Vector3>();
            var outUv = new List<Vector2>();
            var tris = new List<int>();
            var cache = new Dictionary<string, int>();
            foreach (var line in lines)
            {
                var p = line.Split(new[] { ' ' }, StringSplitOptions.RemoveEmptyEntries);
                if (p.Length < 3) continue;
                if (p[0] == "v") positions.Add(new Vector3(F(p[1]), F(p[2]), F(p[3])));
                else if (p[0] == "vt") uvs.Add(new Vector2(F(p[1]), F(p[2])));
                else if (p[0] == "f")
                {
                    var ids = new int[p.Length - 1];
                    for (var i = 1; i < p.Length; i++) ids[i - 1] = Vertex(p[i], positions, uvs, outPos, outUv, cache);
                    for (var i = 1; i < ids.Length - 1; i++) { tris.Add(ids[0]); tris.Add(ids[i]); tris.Add(ids[i + 1]); }
                }
            }
            target.Pos = outPos.ToArray();
            target.Uv = outUv.ToArray();
            target.Tris = tris.ToArray();
        }

        private static float F(string s) => float.Parse(s, CultureInfo.InvariantCulture);

        private static int Vertex(string token, List<Vector3> positions, List<Vector2> uvs, List<Vector3> outPos, List<Vector2> outUv, Dictionary<string, int> cache)
        {
            if (cache.TryGetValue(token, out var known)) return known;
            var f = token.Split('/');
            outPos.Add(positions[int.Parse(f[0], CultureInfo.InvariantCulture) - 1]);
            outUv.Add(f.Length > 1 && f[1].Length > 0 ? uvs[int.Parse(f[1], CultureInfo.InvariantCulture) - 1] : Vector2.zero);
            return cache[token] = outPos.Count - 1;
        }

        /// <summary>The mesh with mirror, size, rotation and offset baked into the vertices.</summary>
        private static Mesh Build(Loaded source, HatOptions o)
        {
            var rotation = Quaternion.Euler(o.RotX, o.RotY, o.RotZ);
            var offset = new Vector3(o.OffX, o.OffY, o.OffZ);
            var pos = new Vector3[source.Pos.Length];
            var uv = new Vector2[source.Uv.Length];
            for (var i = 0; i < pos.Length; i++)
            {
                var p = source.Pos[i];
                if (o.MirrorX) p.x = -p.x;
                pos[i] = rotation * (p * o.Scale) + offset;
                uv[i] = o.FlipV ? new Vector2(source.Uv[i].x, 1f - source.Uv[i].y) : source.Uv[i];
            }
            var tris = (int[])source.Tris.Clone();
            if (o.MirrorX)
            {
                for (var i = 0; i + 2 < tris.Length; i += 3)
                {
                    var swap = tris[i + 1];
                    tris[i + 1] = tris[i + 2];
                    tris[i + 2] = swap;
                }
            }
            var mesh = new Mesh();
            if (pos.Length > 65000) mesh.indexFormat = UnityEngine.Rendering.IndexFormat.UInt32;
            mesh.vertices = pos;
            mesh.uv = uv;
            mesh.triangles = tris;
            mesh.RecalculateNormals();
            mesh.RecalculateBounds();
            PrepareSway(source, o, pos, rotation, offset);
            return mesh;
        }

        /// <summary>
        /// Finds the ribbons: welded-vertex islands that are flat across x (under 0.03 thick), sit to the side (|x| over 0.1)
        /// and reach down into the lowest third of the model. Each ribbon hangs from its top edge; the lower a vertex, the more it swings.
        /// </summary>
        private static void PrepareSway(Loaded s, HatOptions o, Vector3[] finalPos, Quaternion rotation, Vector3 offset)
        {
            s.Sway = false;
            s.RibbonVertex = null;
            if (!o.Sway) return;
            var n = s.Pos.Length;
            var weld = new int[n];
            var keys = new Dictionary<Vector3Int, int>();
            for (var i = 0; i < n; i++)
            {
                var p = s.Pos[i];
                var key = new Vector3Int(Mathf.RoundToInt(p.x * 10000f), Mathf.RoundToInt(p.y * 10000f), Mathf.RoundToInt(p.z * 10000f));
                if (!keys.TryGetValue(key, out var id)) { id = keys.Count; keys[key] = id; }
                weld[i] = id;
            }
            var parent = new int[keys.Count];
            for (var i = 0; i < parent.Length; i++) parent[i] = i;
            for (var i = 0; i + 2 < s.Tris.Length; i += 3)
            {
                Union(parent, weld[s.Tris[i]], weld[s.Tris[i + 1]]);
                Union(parent, weld[s.Tris[i]], weld[s.Tris[i + 2]]);
            }
            var min = new Dictionary<int, Vector3>();
            var max = new Dictionary<int, Vector3>();
            float lowest = float.MaxValue, highest = float.MinValue;
            for (var i = 0; i < n; i++)
            {
                var r = Find(parent, weld[i]);
                var p = s.Pos[i];
                min[r] = min.TryGetValue(r, out var a) ? Vector3.Min(a, p) : p;
                max[r] = max.TryGetValue(r, out var b) ? Vector3.Max(b, p) : p;
                lowest = Mathf.Min(lowest, p.z);
                highest = Mathf.Max(highest, p.z);
            }
            var ribbonRoots = new Dictionary<int, int>();
            foreach (var r in min.Keys)
            {
                var lo = min[r];
                var hi = max[r];
                var cx = (lo.x + hi.x) * 0.5f;
                if (hi.x - lo.x < 0.03f && Mathf.Abs(cx) > 0.1f && lo.z < lowest + (highest - lowest) * 0.34f && hi.z - lo.z > 0.1f)
                    ribbonRoots[r] = ribbonRoots.Count;
            }
            if (ribbonRoots.Count == 0) { Debug.Log("[Ro3Emu.HatSwap] sway=1 but no ribbons found"); return; }
            var list = new List<int>();
            for (var i = 0; i < n; i++)
                if (ribbonRoots.ContainsKey(Find(parent, weld[i]))) list.Add(i);
            s.RibbonVertex = list.ToArray();
            s.Hinge = new Vector3[list.Count];
            s.Weight = new float[list.Count];
            s.Phase = new float[list.Count];
            for (var k = 0; k < list.Count; k++)
            {
                var i = list[k];
                var r = Find(parent, weld[i]);
                var lo = min[r];
                var hi = max[r];
                var hinge = new Vector3((lo.x + hi.x) * 0.5f, (lo.y + hi.y) * 0.5f, hi.z);
                if (o.MirrorX) hinge.x = -hinge.x;
                s.Hinge[k] = rotation * (hinge * o.Scale) + offset;
                s.Weight[k] = Mathf.Clamp01((hi.z - s.Pos[i].z) / Mathf.Max(0.0001f, hi.z - lo.z));
                s.Phase[k] = ribbonRoots[r] * 1.7f;
            }
            s.Base = (Vector3[])finalPos.Clone();
            s.Current = (Vector3[])finalPos.Clone();
            s.AxisFront = rotation * Vector3.right;
            s.AxisSide = rotation * Vector3.up;
            s.SwayAmp = o.SwayAmp;
            s.SwaySpeed = o.SwaySpeed;
            s.Sway = true;
            Debug.Log("[Ro3Emu.HatSwap] sway: " + ribbonRoots.Count + " ribbons, " + list.Count + " vertices");
        }

        private static int Find(int[] parent, int x)
        {
            while (parent[x] != x) { parent[x] = parent[parent[x]]; x = parent[x]; }
            return x;
        }

        private static void Union(int[] parent, int a, int b)
        {
            a = Find(parent, a);
            b = Find(parent, b);
            if (a != b) parent[a] = b;
        }
    }
}
