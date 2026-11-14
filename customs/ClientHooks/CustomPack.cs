using System;
using System.Collections.Generic;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using UnityEngine;

namespace Ro3Emu.ClientHooks
{
    /// <summary>
    /// Reads the custom files (models, textures, icons, settings) either loose from <c>ro3emu\...</c> or, when a loose file does
    /// not exist, from the scrambled pack <c>ro3emu\eclipse.dat</c> that tools\custom-items\pack_custom.py builds. Loose files
    /// win, so tuning with the hat studio keeps working. The pack is not hidden from someone who reverse-engineers this DLL;
    /// it only keeps the files from being browsed and previewed in a folder.
    /// </summary>
    internal static class CustomPack
    {
        // The same secret as in tools\custom-items\pack_custom.py. Change both, then rebuild the pack, for a release.
        private const string Secret = "Eclipse|custom|pack|1";
        private const string PackName = "eclipse.dat";
        private const int HeaderSize = 24;

        private static readonly object Gate = new object();
        private static bool _loaded;
        private static string _root;
        private static string _packFile;
        private static byte[] _key;
        private static byte[] _nonce;
        private static Dictionary<string, long[]> _index;

        /// <summary>True when a pack was found and its index could be read.</summary>
        internal static bool Available
        {
            get
            {
                Load();
                return _index != null;
            }
        }

        internal static bool Exists(string path)
        {
            if (File.Exists(path)) return true;
            Load();
            return _index != null && _index.ContainsKey(Relative(path));
        }

        internal static long Stamp(string path)
        {
            if (File.Exists(path)) return File.GetLastWriteTimeUtc(path).Ticks;
            Load();
            return _index != null && _index.ContainsKey(Relative(path)) ? 1 : 0;
        }

        internal static byte[] ReadAllBytes(string path)
        {
            if (File.Exists(path)) return File.ReadAllBytes(path);
            Load();
            var data = _index == null ? null : Read(Relative(path));
            if (data == null) throw new FileNotFoundException("custom file not found", path);
            return data;
        }

        internal static string[] ReadAllLines(string path) => ReadAllLines(path, Encoding.UTF8);

        internal static string[] ReadAllLines(string path, Encoding encoding)
        {
            if (File.Exists(path)) return File.ReadAllLines(path, encoding);
            var text = encoding.GetString(ReadAllBytes(path));
            var lines = text.Replace("\r\n", "\n").Split('\n');
            if (lines.Length > 0 && lines[lines.Length - 1].Length == 0) Array.Resize(ref lines, lines.Length - 1);
            return lines;
        }

        private static string Relative(string path)
        {
            var p = path.Replace('\\', '/');
            var root = _root.Replace('\\', '/').TrimEnd('/') + "/";
            if (p.StartsWith(root, StringComparison.OrdinalIgnoreCase)) p = p.Substring(root.Length);
            return p.ToLowerInvariant();
        }

        private static void Load()
        {
            if (_loaded) return;
            lock (Gate)
            {
                if (_loaded) return;
                try
                {
                    _root = Path.Combine(Path.GetDirectoryName(Application.dataPath) ?? "", "ro3emu");
                    _packFile = Path.Combine(_root, PackName);
                    _loaded = true; // from here on a missing or broken pack just means "loose files only"
                    if (!File.Exists(_packFile)) return;
                    using (var sha = SHA256.Create()) _key = sha.ComputeHash(Encoding.UTF8.GetBytes(Secret));
                    using (var stream = new FileStream(_packFile, FileMode.Open, FileAccess.Read, FileShare.Read))
                    {
                        var header = new byte[HeaderSize];
                        if (stream.Read(header, 0, HeaderSize) != HeaderSize || header[0] != (byte)'E' || header[1] != (byte)'C' || header[2] != (byte)'P' || header[3] != (byte)'1')
                        {
                            Debug.Log("[Ro3Emu.CustomPack] " + PackName + " is not a pack file");
                            return;
                        }
                        _nonce = new byte[16];
                        Buffer.BlockCopy(header, 4, _nonce, 0, 16);
                        var indexLength = BitConverter.ToInt32(header, 20);
                        var raw = new byte[indexLength];
                        if (stream.Read(raw, 0, indexLength) != indexLength) return;
                        Crypt(raw, indexLength, 0);
                        var index = new Dictionary<string, long[]>(StringComparer.Ordinal);
                        using (var reader = new BinaryReader(new MemoryStream(raw)))
                        {
                            var count = reader.ReadInt32();
                            for (var i = 0; i < count; i++)
                            {
                                var nameLength = reader.ReadInt32();
                                var name = Encoding.UTF8.GetString(reader.ReadBytes(nameLength));
                                var offset = reader.ReadInt64();
                                var length = reader.ReadInt32();
                                index[name] = new[] { offset, length };
                            }
                        }
                        _index = index;
                        Debug.Log("[Ro3Emu.CustomPack] " + PackName + ": " + index.Count + " files");
                    }
                }
                catch (Exception e)
                {
                    _index = null;
                    Debug.Log("[Ro3Emu.CustomPack] cannot read the pack: " + e.GetType().Name + ": " + e.Message);
                }
            }
        }

        private static byte[] Read(string name)
        {
            if (!_index.TryGetValue(name, out var entry)) return null;
            lock (Gate)
            {
                using (var stream = new FileStream(_packFile, FileMode.Open, FileAccess.Read, FileShare.Read))
                {
                    var data = new byte[(int)entry[1]];
                    stream.Seek(HeaderSize + entry[0], SeekOrigin.Begin);
                    var done = 0;
                    while (done < data.Length)
                    {
                        var got = stream.Read(data, done, data.Length - done);
                        if (got <= 0) return null;
                        done += got;
                    }
                    Crypt(data, data.Length, entry[0]);
                    return data;
                }
            }
        }

        /// <summary>XOR with a keystream: block n = HMAC-SHA256(key, nonce + n); <paramref name="position"/> is where data[0] sits in the payload.</summary>
        private static void Crypt(byte[] data, int count, long position)
        {
            using (var hmac = new HMACSHA256(_key))
            {
                var block = new byte[_nonce.Length + 8];
                Buffer.BlockCopy(_nonce, 0, block, 0, _nonce.Length);
                var cached = -1L;
                byte[] stream = null;
                for (var i = 0; i < count; i++)
                {
                    var at = position + i;
                    var number = at / 32;
                    if (number != cached)
                    {
                        for (var b = 0; b < 8; b++) block[_nonce.Length + b] = (byte)(number >> (8 * b));
                        stream = hmac.ComputeHash(block);
                        cached = number;
                    }
                    data[i] ^= stream[(int)(at % 32)];
                }
            }
        }
    }
}
