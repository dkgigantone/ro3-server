using System.Globalization;
using System.IO;

namespace Ro3Emu.ClientHooks
{
    /// <summary>
    /// How a custom hat sits on the head: the settings file <c>ro3emu\hats\&lt;Name&gt;.txt</c> (size, rotation in degrees,
    /// offset, texture flip, left/right mirror). Read by <see cref="HatSwap"/>, edited and written by the hat studio.
    /// </summary>
    internal sealed class HatOptions
    {
        public float Scale = 1f;
        public float RotX;
        public float RotY;
        public float RotZ;
        public float OffX;
        public float OffY;
        public float OffZ;
        public bool FlipV;
        public bool MirrorX;
        /// <summary>Ribbon sway (optional): flat hanging strips are swung by <see cref="HatSwap"/>.</summary>
        public bool Sway;
        /// <summary>sway=2: any separate piece hanging below the hat body swings (ribbons that are not thin side strips).</summary>
        public bool SwayAny;
        public float SwayAmp = 10f;
        public float SwaySpeed = 2.2f;
        /// <summary>The item has see-through parts (tinted glass): its texture's alpha is used.</summary>
        public bool Transparent;

        public static HatOptions Load(string path)
        {
            var options = new HatOptions();
            if (!CustomPack.Exists(path)) return options;
            foreach (var line in CustomPack.ReadAllLines(path))
            {
                var eq = line.IndexOf('=');
                if (eq <= 0) continue;
                var values = line.Substring(eq + 1).Trim().Split(',');
                switch (line.Substring(0, eq).Trim().ToLowerInvariant())
                {
                    case "scale": options.Scale = Part(values, 0, 1f); break;
                    case "rotate": options.RotX = Part(values, 0, 0f); options.RotY = Part(values, 1, 0f); options.RotZ = Part(values, 2, 0f); break;
                    case "offset": options.OffX = Part(values, 0, 0f); options.OffY = Part(values, 1, 0f); options.OffZ = Part(values, 2, 0f); break;
                    case "flipv": options.FlipV = Part(values, 0, 0f) > 0f; break;
                    case "mirrorx": options.MirrorX = Part(values, 0, 0f) > 0f; break;
                    case "sway": options.Sway = Part(values, 0, 0f) > 0f; options.SwayAny = Part(values, 0, 0f) >= 2f; break;
                    case "swayamp": options.SwayAmp = Part(values, 0, 10f); break;
                    case "swayspeed": options.SwaySpeed = Part(values, 0, 2.2f); break;
                    case "transparent": options.Transparent = Part(values, 0, 0f) > 0f; break;
                }
            }
            return options;
        }

        public void Save(string path)
        {
            var inv = CultureInfo.InvariantCulture;
            var lines = new System.Collections.Generic.List<string>
            {
                "scale=" + Scale.ToString("0.###", inv),
                "rotate=" + RotX.ToString("0.#", inv) + "," + RotY.ToString("0.#", inv) + "," + RotZ.ToString("0.#", inv),
                "offset=" + OffX.ToString("0.###", inv) + "," + OffY.ToString("0.###", inv) + "," + OffZ.ToString("0.###", inv),
                "flipv=" + (FlipV ? "1" : "0"),
                "mirrorx=" + (MirrorX ? "1" : "0"),
            };
            if (Sway)
            {
                lines.Add(SwayAny ? "sway=2" : "sway=1");
                lines.Add("swayamp=" + SwayAmp.ToString("0.#", inv));
                lines.Add("swayspeed=" + SwaySpeed.ToString("0.##", inv));
            }
            if (Transparent) lines.Add("transparent=1");
            File.WriteAllText(path, string.Join("\r\n", lines) + "\r\n");
        }

        public HatOptions Clone() => (HatOptions)MemberwiseClone();

        public bool SameAs(HatOptions other) =>
            Scale == other.Scale && RotX == other.RotX && RotY == other.RotY && RotZ == other.RotZ
            && OffX == other.OffX && OffY == other.OffY && OffZ == other.OffZ && FlipV == other.FlipV && MirrorX == other.MirrorX
            && Sway == other.Sway && SwayAny == other.SwayAny && SwayAmp == other.SwayAmp && SwaySpeed == other.SwaySpeed && Transparent == other.Transparent;

        private static float Part(string[] values, int index, float fallback) =>
            index < values.Length && float.TryParse(values[index].Trim(), NumberStyles.Float, CultureInfo.InvariantCulture, out var number) ? number : fallback;
    }
}
