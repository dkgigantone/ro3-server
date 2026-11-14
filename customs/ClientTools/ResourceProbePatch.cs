using Mono.Cecil;
using Mono.Cecil.Cil;

namespace Ro3Emu.ClientTools;

/// <summary>
/// HappyEngine.Resource.dll -> Ro3Emu.ClientHooks ResourceProbe (read-only): the model name given to
/// PrefabPoolManager.TryGetInstanceFromPool and the icon name given to ResourceInfo.GetSprite pass through the probe at
/// method entry (arg = probe(arg)). The probe returns the name unchanged and only logs it.
/// </summary>
public static class ResourceProbePatch
{
    public static string Patch(ModuleDefinition module, string hooksDll)
    {
        using var hooks = AssemblyDefinition.ReadAssembly(hooksDll, new ReaderParameters { InMemory = true });
        var probe = hooks.MainModule.GetType("Ro3Emu.ClientHooks.ResourceProbe") ?? throw new InvalidOperationException("ResourceProbe not found in the hooks");
        MethodReference Hook(string name) => module.ImportReference(probe.Methods.Single(m => m.Name == name && m.Parameters.Count == 1));

        MapFirstArgument(Method(module, "HappyEngine.Runtime.NewRuntimeSystem.PrefabPoolManager", "TryGetInstanceFromPool"), Hook("OnPrefab"));
        MapFirstArgument(Method(module, "HappyEngine.HLAsset.ResourceInfo", "GetSprite"), Hook("OnSprite"));
        MapFirstArgument(Method(module, "HappyEngine.HLAsset.AssetInfo", "GetSprite"), Hook("OnAssetSprite"));
        MapFirstArgument(Method(module, "HappyEngine.Runtime.NewRuntimeSystem.BundleLoader", "LoadAssetAsync"), Hook("OnBundleLoad"));
        MapFirstArgument(Method(module, "HappyEngine.Runtime.NewRuntimeSystem.AssetElement", "LoadAssetAsync"), Hook("OnElementLoad"));
        MapFirstArgument(Method(module, "HappyEngine.HLAsset.ResourceManifest", "TryGetAtlas"), Hook("OnAtlas"));
        var assetSet = module.ImportReference(probe.Methods.Single(m => m.Name == "OnAssetSet" && m.Parameters.Count == 2));
        MapAssetSetter(Method(module, "HappyEngine.Runtime.NewRuntimeSystem.AssetElement", "set_Asset"), assetSet);
        var custom = hooks.MainModule.GetType("Ro3Emu.ClientHooks.CustomAssets") ?? throw new InvalidOperationException("CustomAssets not found in the hooks");
        InsertCustomLoad(Method(module, "HappyEngine.Runtime.NewRuntimeSystem.AssetLoader", "LoadAssetByPriority"), module.ImportReference(custom.Methods.Single(m => m.Name == "TryLoad")));
        return "custom assets: AssetLoader.LoadAssetByPriority -> CustomAssets.TryLoad; resource probe: TryGetInstanceFromPool, ResourceInfo/AssetInfo.GetSprite, BundleLoader/AssetElement.LoadAssetAsync, TryGetAtlas, AssetElement.set_Asset -> ResourceProbe";
    }

    /// <summary>Render.dll: DOTSPathResolver.ResolvePath returns CustomAssets.ResolveDots(path) when that is not null.</summary>
    public static string PatchRender(ModuleDefinition module, string hooksDll)
    {
        using var hooks = AssemblyDefinition.ReadAssembly(hooksDll, new ReaderParameters { InMemory = true });
        var custom = hooks.MainModule.GetType("Ro3Emu.ClientHooks.CustomAssets") ?? throw new InvalidOperationException("CustomAssets not found in the hooks");
        var hook = module.ImportReference(custom.Methods.Single(m => m.Name == "ResolveDots"));
        var method = Method(module, "DOTSPathResolver", "ResolvePath");
        var il = method.Body.GetILProcessor();
        var first = method.Body.Instructions[0];
        var drop = il.Create(OpCodes.Pop);
        il.InsertBefore(first, il.Create(OpCodes.Ldarg_0));
        il.InsertBefore(first, il.Create(OpCodes.Call, hook));
        il.InsertBefore(first, il.Create(OpCodes.Dup));
        il.InsertBefore(first, il.Create(OpCodes.Brfalse, drop));
        il.InsertBefore(first, il.Create(OpCodes.Ret));
        il.InsertBefore(first, drop);
        return "custom models: DOTSPathResolver.ResolvePath -> CustomAssets.ResolveDots";
    }

    private static MethodDefinition Method(ModuleDefinition module, string type, string name) =>
        module.GetType(type)?.Methods.SingleOrDefault(m => m.Name == name && m.HasBody)
        ?? throw new InvalidOperationException($"{type}.{name} not found");

    /// <summary>
    /// At the entry of AssetLoader.LoadAssetByPriority: <c>var e = hook(this, path, priority, cb); if (e != null) return e;</c>
    /// (the hook returns an AssetElement of its own for custom paths and null for every other path).
    /// </summary>
    private static void InsertCustomLoad(MethodDefinition method, MethodReference hook)
    {
        var il = method.Body.GetILProcessor();
        var first = method.Body.Instructions[0];
        var drop = il.Create(OpCodes.Pop);
        il.InsertBefore(first, il.Create(OpCodes.Ldarg_0));
        il.InsertBefore(first, il.Create(OpCodes.Ldarg_1));
        il.InsertBefore(first, il.Create(OpCodes.Ldarg_2));
        il.InsertBefore(first, il.Create(OpCodes.Ldarg_3));
        il.InsertBefore(first, il.Create(OpCodes.Call, hook));
        il.InsertBefore(first, il.Create(OpCodes.Dup));
        il.InsertBefore(first, il.Create(OpCodes.Brfalse, drop));
        il.InsertBefore(first, il.Create(OpCodes.Castclass, method.ReturnType));
        il.InsertBefore(first, il.Create(OpCodes.Ret));
        il.InsertBefore(first, drop);
    }

    /// <summary>value = hook(this, value) at the entry of an instance setter.</summary>
    private static void MapAssetSetter(MethodDefinition method, MethodReference hook)
    {
        var il = method.Body.GetILProcessor();
        var first = method.Body.Instructions[0];
        var parameter = method.Parameters[0];
        il.InsertBefore(first, il.Create(OpCodes.Ldarg_0));
        il.InsertBefore(first, il.Create(OpCodes.Ldarg, parameter));
        il.InsertBefore(first, il.Create(OpCodes.Call, hook));
        il.InsertBefore(first, il.Create(OpCodes.Starg, parameter));
    }

    /// <summary>First argument = hook(first argument) at method entry.</summary>
    private static void MapFirstArgument(MethodDefinition method, MethodReference hook)
    {
        var il = method.Body.GetILProcessor();
        var first = method.Body.Instructions[0];
        var parameter = method.Parameters[0];
        il.InsertBefore(first, il.Create(OpCodes.Ldarg, parameter));
        il.InsertBefore(first, il.Create(OpCodes.Call, hook));
        il.InsertBefore(first, il.Create(OpCodes.Starg, parameter));
    }
}
