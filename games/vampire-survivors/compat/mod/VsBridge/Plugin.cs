using System;
using System.Collections.Concurrent;
using System.Threading;
using BepInEx;
using BepInEx.Logging;
using BepInEx.Unity.IL2CPP;
using HarmonyLib;
using Il2CppInterop.Runtime.Injection;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace VsBridge
{
    /// <summary>
    /// BepInEx 6 IL2CPP bridge for Vampire Survivors.
    ///
    /// The game stays in the background: this plugin reads the live run state on
    /// Unity's main thread and accepts movement / menu choices over a loopback
    /// HTTP API.  It never calls SetForegroundWindow or SendInput.
    /// </summary>
    [BepInPlugin(Guid, "VS Bridge", Version)]
    public class Plugin : BasePlugin
    {
        public const string Guid = "dafeiyu.vsbridge";
        public const string Version = "0.1.0";
        public const int ApiVersion = 1;

        public static Plugin Instance { get; private set; }
        public static ManualLogSource LogSrc { get; private set; }

        internal static bool Automation;
        internal static Vector2 Move = Vector2.zero;
        internal static float MoveUntil;
        internal static string LastChoice = "stay";

        internal static readonly ConcurrentQueue<JObject> Actions =
            new ConcurrentQueue<JObject>();

        internal BridgeBehaviour Behaviour;
        internal MenuDriver Menu;
        internal HttpApi Http;

        private Harmony _harmony;
        private float _nextScan;
        private volatile string _stateJson =
            "{\"ok\":true,\"ready\":false,\"api\":" + ApiVersion + "}";

        public override void Load()
        {
            Instance = this;
            LogSrc = Log;
            // Unity defaults to pausing updates when the window loses focus.
            // The bridge must keep sampling while the game is in the background.
            Application.runInBackground = true;

            if (!ClassInjector.IsTypeRegisteredInIl2Cpp<BridgeBehaviour>())
                ClassInjector.RegisterTypeInIl2Cpp<BridgeBehaviour>();
            Behaviour = AddComponent<BridgeBehaviour>();
            Menu = new MenuDriver(Behaviour);

            _harmony = new Harmony(Guid);
            _harmony.PatchAll(typeof(Plugin).Assembly);

            Http = new HttpApi(this);
            Http.Start();

            Log.LogInfo($"{Guid} {Version} loaded; API on {Http.Url}; "
                        + "no window focus / no keyboard injection");
        }

        public override bool Unload()
        {
            try { Http?.Stop(); } catch { }
            try { _harmony?.UnpatchSelf(); } catch { }
            return true;
        }

        /// <summary>Called by BridgeBehaviour.Update on Unity's main thread.</summary>
        internal void Tick()
        {
            DrainActions();
            float now = Time.realtimeSinceStartup;
            if (now >= _nextScan)
            {
                _nextScan = now + 1f;
                Menu?.ScanExisting();
            }
            Menu?.Update();
            if (MoveUntil > 0f && Time.realtimeSinceStartup >= MoveUntil)
            {
                Move = Vector2.zero;
                MoveUntil = 0f;
                LastChoice = "stay";
            }
            StateSampler.Tick();
        }

        internal void DrainActions()
        {
            while (Actions.TryDequeue(out var msg))
            {
                var type = (string)msg["type"] ?? "";
                try
                {
                    switch (type)
                    {
                        case "move":
                            Automation = true;
                            Move = new Vector2((float?)msg["dx"] ?? 0f,
                                               (float?)msg["dy"] ?? 0f);
                            MoveUntil = Time.realtimeSinceStartup
                                        + Mathf.Clamp((float?)msg["seconds"] ?? 0.5f,
                                                      0.05f, 3f);
                            LastChoice = (string)msg["choice"] ?? "move";
                            break;
                        case "choose":
                            Menu?.SetChoice((int?)msg["index"] ?? 0);
                            break;
                        case "press":
                            Menu?.Press((string)msg["key"] ?? "");
                            break;
                        case "control":
                            Automation = (bool?)msg["automation"] ?? true;
                            if (!Automation) StopMoving();
                            break;
                        case "start":
                            Automation = true;
                            Menu?.RequestStart();
                            break;
                        case "stop":
                            Automation = false;
                            StopMoving();
                            Menu?.Cancel();
                            break;
                        default:
                            LogSrc?.LogWarning($"unknown action type={type}");
                            break;
                    }
                }
                catch (Exception ex)
                {
                    LogSrc?.LogWarning($"action {type} failed: {ex.Message}");
                }
            }
        }

        private static void StopMoving()
        {
            Move = Vector2.zero;
            MoveUntil = 0f;
            LastChoice = "stay";
        }

        internal void SetStateJson(string json) => _stateJson = json;

        internal string StateJson => _stateJson;

        internal static void Enqueue(JObject action)
        {
            if (action != null) Actions.Enqueue(action);
        }
    }
}
