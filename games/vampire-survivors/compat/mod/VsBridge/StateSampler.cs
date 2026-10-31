using System;
using System.Collections.Generic;
using HarmonyLib;
using Newtonsoft.Json.Linq;
using UnityEngine;
using VampireSurvivors;
using VampireSurvivors.Framework;
using VampireSurvivors.Objects;
using VampireSurvivors.Objects.Characters;
using VampireSurvivors.Objects.Pickups;
using VampireSurvivors.Tools;
using VampireSurvivors.UI;

namespace VsBridge
{
    /// <summary>Reads the live run on Unity's main thread and publishes JSON.</summary>
    internal static class StateSampler
    {
        private const float Interval = 0.1f;
        private const int MaxEntities = 200;
        private static float _next;
        private static Vector3? _prevPos;

        internal static void Tick()
        {
            float now = Time.realtimeSinceStartup;
            if (now < _next) return;
            _next = now + Interval;
            try
            {
                Plugin.Instance?.SetStateJson(Build().ToString(Newtonsoft.Json.Formatting.None));
            }
            catch (Exception ex)
            {
                Plugin.LogSrc?.LogWarning($"state build failed: {ex.Message}");
                Plugin.Instance?.SetStateJson(
                    "{\"ok\":true,\"ready\":true,\"api\":1,\"state\":\"unknown\","
                    + "\"error\":" + Newtonsoft.Json.JsonConvert.ToString(ex.Message) + "}");
            }
        }

        internal static void ResetTracking() => _prevPos = null;

        private static bool RunActive()
        {
            var gm = GM.Core;
            return gm != null && gm.Player != null && gm.Stage != null
                   && !gm.IsPaused && !gm.Player.IsDead;
        }

        internal static JObject Build()
        {
            var gm = GM.Core;
            var player = gm != null ? gm.Player : null;
            var stage = gm != null ? gm.Stage : null;
            bool active = RunActive();

            string state;
            if (gm == null) state = "menu";
            else if (player != null && player.IsDead) state = "game_over";
            else if (Plugin.Instance?.Menu != null && Plugin.Instance.Menu.LevelUpActive)
                state = "level_up";
            else if (gm.IsPaused) state = "paused";
            else if (Plugin.Instance?.Menu != null && Plugin.Instance.Menu.Kind != "")
                state = Plugin.Instance.Menu.Kind;
            else if (active) state = "playing";
            else state = "menu";

            var root = new JObject
            {
                ["ok"] = true,
                ["ready"] = true,
                ["api"] = Plugin.ApiVersion,
                ["state"] = state,
                ["automation"] = Plugin.Automation,
                ["last"] = Plugin.LastChoice,
                ["run"] = new JObject
                {
                    ["active"] = active,
                    ["started"] = active,
                },
                ["player"] = player != null ? Player(player, gm, stage) : null,
                ["menu"] = Plugin.Instance?.Menu?.Json() ?? JValue.CreateNull(),
                ["enemies"] = new JArray(),
                ["gems"] = new JArray(),
                ["pickups"] = new JArray(),
                ["screen"] = new JObject { ["half_w"] = 0, ["half_h"] = 0 },
            };

            if (!active || stage == null || player == null) return root;

            Vector3 pp = player.transform.position;
            float moved = _prevPos.HasValue
                ? R(Vector3.Distance(pp, _prevPos.Value))
                : 999f;
            _prevPos = pp;
            if (root["player"] is JObject playerJson) playerJson["moved"] = moved;

            Camera cam = stage._mainCamera != null ? stage._mainCamera : Camera.main;
            if (cam != null)
            {
                Bounds b = cam.OrthographicBounds();
                root["screen"] = new JObject
                {
                    ["half_w"] = R(b.extents.x),
                    ["half_h"] = R(b.extents.y),
                };
            }

            var enemies = new List<(float d, float dx, float dy, EnemyController e)>();
            foreach (var e in stage.GetAllEnemiesInScreenBounds())
            {
                if (e == null || e.IsUnitDead()) continue;
                Vector3 ep = e.transform.position;
                float dx = ep.x - pp.x, dy = ep.y - pp.y;
                enemies.Add((dx * dx + dy * dy, dx, dy, e));
            }
            root["enemies"] = Nearest(enemies, MaxEntities, (dx, dy, e) => new JObject
            {
                ["x"] = R(dx), ["y"] = R(dy), ["hp"] = R(e.CurrentHealth()),
                ["type"] = e.EnemyType.ToString(), ["boss"] = e.IsBoss,
            });

            var gems = new List<(float d, float dx, float dy, Pickup g)>();
            foreach (var g in stage.GetAllGemsInScreenBounds())
            {
                if (g == null) continue;
                Vector3 gp = g.transform.position;
                float dx = gp.x - pp.x, dy = gp.y - pp.y;
                gems.Add((dx * dx + dy * dy, dx, dy, g));
            }
            root["gems"] = Nearest(gems, MaxEntities, (dx, dy, g) => new JObject
            {
                ["x"] = R(dx), ["y"] = R(dy), ["value"] = R(g.Value),
            });

            var pickups = new List<(float d, float dx, float dy, Pickup p)>();
            foreach (var pk in stage.GetAllPickupsInScreenBounds())
            {
                if (pk == null) continue;
                Vector3 kp = pk.transform.position;
                float dx = kp.x - pp.x, dy = kp.y - pp.y;
                pickups.Add((dx * dx + dy * dy, dx, dy, pk));
            }
            root["pickups"] = Nearest(pickups, MaxEntities, (dx, dy, pk) => new JObject
            {
                ["x"] = R(dx), ["y"] = R(dy), ["kind"] = pk.PickupType.ToString(),
            });

            return root;
        }

        private static JObject Player(CharacterController p, GameManager gm, Stage stage)
        {
            var o = new JObject
            {
                ["x"] = R(p.transform.position.x),
                ["y"] = R(p.transform.position.y),
                ["hp"] = R(p.CurrentHealth()),
                ["max_hp"] = R(p.MaxHp()),
                ["level"] = p.Level,
                ["xp"] = R(p.Xp),
                ["xp_to_next"] = gm?.LevelUpFactory != null
                    ? R(gm.LevelUpFactory.XpRequiredToLevelUp) : 0,
                ["minute"] = stage != null ? stage.CurrentMinute : 0,
                ["seconds"] = gm != null ? R(gm.SurvivedSeconds) : 0,
                ["character"] = p.CharacterType.ToString(),
                ["stage"] = gm?.PlayerOptions?.Config != null
                    ? gm.PlayerOptions.Config.SelectedStage.ToString() : "",
                ["weapons"] = Equip(p.WeaponsManager),
                ["passives"] = Equip(p.AccessoriesManager),
            };
            var hud = UnityEngine.Object.FindFirstObjectByType<MainGamePage>();
            string kills = hud != null && hud.KillsText != null ? hud.KillsText.text : null;
            if (!string.IsNullOrEmpty(kills))
            {
                var digits = new System.Text.StringBuilder();
                foreach (char c in kills) if (char.IsDigit(c)) digits.Append(c);
                if (int.TryParse(digits.ToString(), out var n)) o["kills"] = n;
            }
            return o;
        }

        private static JArray Equip(EquipmentManager m)
        {
            var a = new JArray();
            if (m == null) return a;
            foreach (var eq in m.ActiveEquipment)
            {
                if (eq == null) continue;
                a.Add(new JObject
                {
                    ["id"] = eq.Type.ToString(),
                    ["level"] = eq.Level,
                    ["max"] = eq.IsMaxLevel(),
                });
            }
            return a;
        }

        private static JArray Nearest<T>(List<(float d, float dx, float dy, T item)> items,
                                        int cap, Func<float, float, T, JObject> build)
        {
            items.Sort((x, y) => x.d.CompareTo(y.d));
            var arr = new JArray();
            for (int i = 0; i < items.Count && i < cap; i++)
            {
                var it = items[i];
                arr.Add(build(it.dx, it.dy, it.item));
            }
            return arr;
        }

        private static float R(float v) => (float)Math.Round(v, 2);
    }

    /// <summary>Replaces Rewired's raw movement vector for the main player.</summary>
    [HarmonyPatch(typeof(CharacterController), "ProcessRawDirection")]
    internal static class ProcessRawDirectionPatch
    {
        private static void Prefix(CharacterController __instance)
        {
            if (!Plugin.Automation) return;
            var gm = GM.Core;
            if (gm == null || __instance != gm.Player) return;
            __instance._currentDirectionRaw = Plugin.Move;
        }
    }
}
