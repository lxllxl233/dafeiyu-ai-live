using System;
using UnityEngine;

namespace VsBridge
{
    /// <summary>Unity main-thread driver for the plugin (IL2CPP needs an IntPtr ctor).</summary>
    public class BridgeBehaviour : MonoBehaviour
    {
        public BridgeBehaviour(IntPtr ptr) : base(ptr) { }

        private void Update() => Plugin.Instance?.Tick();

        private void OnApplicationQuit()
        {
            try { Plugin.Instance?.Http?.Stop(); } catch { }
        }
    }
}
