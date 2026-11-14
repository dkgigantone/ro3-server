using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using UnityEngine;

namespace Ro3Emu.ClientHooks
{
    /// <summary>
    /// Opt-in diagnostic (flag file <c>ro3emu\custom\probe-body.txt</c>): describes what the client's own loader produces for
    /// <c>model_body_novice_m*</c> assets (type, fields, hierarchy) in Player.log and in <c>ro3emu\dump-body</c>. Read-only: the
    /// original assets are loaded and delivered unchanged.
    /// </summary>
    internal static class BodyProbe
    {
        private static readonly HashSet<string> Seen = new HashSet<string>(StringComparer.Ordinal);
        private static bool _checked, _enabled;
        private static int _logs;
        private const BindingFlags Any = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance;

        private static string DumpDir() => Path.Combine(Path.GetDirectoryName(Application.dataPath) ?? "", Path.Combine("ro3emu", "dump-body"));

        internal static bool Wants(string name)
        {
            if (!_checked)
            {
                _checked = true;
                _enabled = File.Exists(Path.Combine(CustomAssets.IconDir(), Path.Combine("..", "probe-body.txt")));
                if (_enabled) Log("probe on (model_body_novice_m*)");
            }
            return _enabled && name.StartsWith("model_body_novice_m", StringComparison.Ordinal) && !Seen.Contains(name);
        }

        internal static void Mark(string name) => Seen.Add(name);

        internal static void Dump(string name, object asset)
        {
            try
            {
                if (asset == null) { Log(name + ": asset is null"); return; }
                var type = asset.GetType();
                var chain = new StringBuilder(type.FullName);
                for (var b = type.BaseType; b != null && b != typeof(object); b = b.BaseType) chain.Append(" : ").Append(b.FullName);
                Log(name + ": " + chain);
                Directory.CreateDirectory(DumpDir());
                if (asset is TextAsset text)
                {
                    File.WriteAllBytes(Path.Combine(DumpDir(), name), text.bytes);
                    Log(name + ": TextAsset " + text.bytes.Length + " bytes written");
                }
                else if (asset is Mesh mesh) DumpMesh(name, mesh);
                else if (asset is GameObject go) DumpPrefab(name, go);
                else if (type.FullName == "HappyEngine.Base.MMapPtr") DumpMap(name, asset, type);
                else DumpFields(name, asset, 0);
            }
            catch (Exception e)
            {
                Log(name + ": dump failed " + e.GetType().Name + ": " + e.Message);
            }
        }

        /// <summary>A memory-mapped custom file (.hskf skeleton, .hacf animation): copy the bytes out for offline reading.</summary>
        private static void DumpMap(string name, object map, Type type)
        {
            var ptr = (IntPtr)type.GetField("<mmapPtr>k__BackingField", Any).GetValue(map);
            var size = (int)type.GetField("<size>k__BackingField", Any).GetValue(map);
            if (ptr == IntPtr.Zero || size <= 0) { Log(name + ": empty map"); return; }
            var bytes = new byte[Math.Min(size, 8000000)];
            Marshal.Copy(ptr, bytes, 0, bytes.Length);
            File.WriteAllBytes(Path.Combine(DumpDir(), name), bytes);
            var head = new StringBuilder();
            for (var i = 0; i < Math.Min(bytes.Length, 64); i++) head.Append(bytes[i].ToString("x2"));
            Log(name + ": map " + size + " bytes written, head " + head);
        }

        private static void DumpMesh(string name, Mesh mesh)
        {
            var poses = new StringBuilder();
            foreach (var m in mesh.bindposes)
            {
                for (var r = 0; r < 4; r++) for (var c = 0; c < 4; c++) poses.Append(m[r, c].ToString("R")).Append(r == 3 && c == 3 ? "\n" : " ");
            }
            Directory.CreateDirectory(DumpDir());
            File.WriteAllText(Path.Combine(DumpDir(), name + ".bindposes.txt"), poses.ToString());
            var sb = new StringBuilder();
            sb.Append("Mesh '").Append(mesh.name).Append("' verts=").Append(mesh.vertexCount).Append(" submeshes=").Append(mesh.subMeshCount)
              .Append(" bindposes=").Append(mesh.bindposes.Length).Append(" readable=").Append(mesh.isReadable).Append(" bounds=").Append(mesh.bounds);
            foreach (var a in mesh.GetVertexAttributes()) sb.Append("\n   attr ").Append(a.attribute).Append(' ').Append(a.format).Append('x').Append(a.dimension).Append(" stream").Append(a.stream);
            Log(name + ": " + sb);
        }

        private static void DumpPrefab(string name, GameObject go)
        {
            var sb = new StringBuilder("GameObject '" + go.name + "' hierarchy:");
            var n = 0;
            foreach (var t in go.GetComponentsInChildren<Transform>(true))
            {
                if (n++ > 70) { sb.Append("\n   ..."); break; }
                var path = t.name;
                for (var p = t.parent; p != null; p = p.parent) path = p.name + "/" + path;
                sb.Append("\n   ").Append(path).Append(" [");
                foreach (var c in t.GetComponents<Component>())
                {
                    if (c == null) { sb.Append("<missing script>,"); continue; }
                    sb.Append(c.GetType().Name);
                    if (!(c is Transform) && !(c is Renderer) && !(c is MeshFilter) && c.GetType().Name != "CapsuleCollider") DumpFields(name + "/" + t.name + "/" + c.GetType().Name, c, 0);
                    if (c is Renderer r)
                    {
                        sb.Append('(');
                        foreach (var m in r.sharedMaterials) sb.Append(m == null ? "null" : m.name + "/" + (m.shader == null ? "?" : m.shader.name)).Append(';');
                        sb.Append(')');
                    }
                    sb.Append(',');
                }
                sb.Append(']');
            }
            Log(name + ": " + sb);
        }

        private static void DumpFields(string name, object obj, int depth)
        {
            var sb = new StringBuilder();
            var count = 0;
            for (var t = obj.GetType(); t != null && t != typeof(object) && t != typeof(UnityEngine.Object); t = t.BaseType)
            {
                foreach (var f in t.GetFields(Any | BindingFlags.DeclaredOnly))
                {
                    if (count++ > 60) { sb.Append("\n   ..."); break; }
                    object v;
                    try { v = f.GetValue(obj); } catch (Exception e) { v = "<" + e.GetType().Name + ">"; }
                    sb.Append("\n   ").Append(f.FieldType.Name).Append(' ').Append(f.Name).Append(" = ").Append(Summary(v));
                    if (v is byte[] bytes && bytes.Length > 0)
                    {
                        File.WriteAllBytes(Path.Combine(DumpDir(), name + "." + f.Name + ".bin"), bytes);
                        sb.Append(" (written)");
                    }
                }
            }
            Log(name + ": fields" + sb);
        }

        private static string Summary(object v)
        {
            if (v == null) return "null";
            if (v is string s) return "\"" + (s.Length > 80 ? s.Substring(0, 80) + "..." : s) + "\"";
            if (v is byte[] b)
            {
                var head = new StringBuilder();
                for (var i = 0; i < Math.Min(b.Length, 48); i++) head.Append(b[i].ToString("x2"));
                return "byte[" + b.Length + "] " + head;
            }
            if (v is Array a) return a.GetType().GetElementType().Name + "[" + a.Length + "]";
            if (v is ICollection c) return v.GetType().Name + " count=" + c.Count;
            var t = v.GetType();
            if (t.IsPrimitive || t.IsEnum || v is Vector3 || v is Quaternion || v is Matrix4x4) return v.ToString();
            return t.FullName;
        }

        private static void Log(string message)
        {
            if (_logs++ < 120) Debug.Log("[Ro3Emu.BodyProbe] " + message);
        }
    }
}
