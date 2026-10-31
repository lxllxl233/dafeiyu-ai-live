using System;
using HarmonyLib;
using VampireSurvivors;
using VampireSurvivors.UI;

namespace VsBridge
{
    internal static class Safe
    {
        public static void Run(string what, Action action)
        {
            try { action(); }
            catch (Exception ex) { Plugin.LogSrc?.LogWarning($"{what} failed: {ex.Message}"); }
        }
    }

    // OnShowStart on BaseUIPage itself receives the base container, not the
    // concrete page; patch each page type separately (as the Linux plugin does).
    [HarmonyPatch(typeof(WarningPage), "OnShowStart")]
    internal static class WarningShow
    {
        private static void Postfix(WarningPage __instance) =>
            Safe.Run("WarningShow", () => Plugin.Instance?.Menu?.PageShown(__instance));
    }

    [HarmonyPatch(typeof(MainMenuPage), "OnShowStart")]
    internal static class MainMenuShow
    {
        private static void Postfix(MainMenuPage __instance) =>
            Safe.Run("MainMenuShow", () => Plugin.Instance?.Menu?.PageShown(__instance));
    }

    [HarmonyPatch(typeof(CharacterSelectionPage), "OnShowStart")]
    internal static class CharacterSelectShow
    {
        private static void Postfix(CharacterSelectionPage __instance) =>
            Safe.Run("CharacterSelectShow", () => Plugin.Instance?.Menu?.PageShown(__instance));
    }

    [HarmonyPatch(typeof(WeaponSelectionPage), "OnShowStart")]
    internal static class WeaponSelectShow
    {
        private static void Postfix(WeaponSelectionPage __instance) =>
            Safe.Run("WeaponSelectShow", () => Plugin.Instance?.Menu?.PageShown(__instance));
    }

    [HarmonyPatch(typeof(StageSelectPage), "OnShowStart")]
    internal static class StageSelectShow
    {
        private static void Postfix(StageSelectPage __instance) =>
            Safe.Run("StageSelectShow", () => Plugin.Instance?.Menu?.PageShown(__instance));
    }

    [HarmonyPatch(typeof(MainGamePage), "OnShowStart")]
    internal static class MainGameShow
    {
        private static void Postfix(MainGamePage __instance) =>
            Safe.Run("MainGameShow", () => Plugin.Instance?.Menu?.PageShown(__instance));
    }

    [HarmonyPatch(typeof(LevelUpPage), "EnableLevelupOptions")]
    internal static class LevelUpPatch
    {
        private static void Postfix(LevelUpPage __instance) =>
            Safe.Run("LevelUpShow", () => Plugin.Instance?.Menu?.PageShown(__instance));
    }

    [HarmonyPatch(typeof(ArcanaMainSelectionPage), "OnShowStart")]
    internal static class ArcanaShow
    {
        private static void Postfix(ArcanaMainSelectionPage __instance) =>
            Safe.Run("ArcanaShow", () => Plugin.Instance?.Menu?.PageShown(__instance));
    }

    [HarmonyPatch(typeof(OpenTreasurePage), "OnShowStart")]
    internal static class TreasureShow
    {
        private static void Postfix(OpenTreasurePage __instance) =>
            Safe.Run("TreasureShow", () => Plugin.Instance?.Menu?.PageShown(__instance));
    }

    [HarmonyPatch(typeof(ItemFoundPage), "OnShowStart")]
    internal static class ItemFoundShow
    {
        private static void Postfix(ItemFoundPage __instance) =>
            Safe.Run("ItemFoundShow", () => Plugin.Instance?.Menu?.PageShown(__instance));
    }

    [HarmonyPatch(typeof(CharacterFoundPage), "OnShowStart")]
    internal static class CharacterFoundShow
    {
        private static void Postfix(CharacterFoundPage __instance) =>
            Safe.Run("CharacterFoundShow", () => Plugin.Instance?.Menu?.PageShown(__instance));
    }

    [HarmonyPatch(typeof(GameOverPage), "OnShowStart")]
    internal static class GameOverShow
    {
        private static void Postfix(GameOverPage __instance) =>
            Safe.Run("GameOverShow", () => Plugin.Instance?.Menu?.PageShown(__instance));
    }

    [HarmonyPatch(typeof(RecapPage), "OnShowStart")]
    internal static class RecapShow
    {
        private static void Postfix(RecapPage __instance) =>
            Safe.Run("RecapShow", () => Plugin.Instance?.Menu?.PageShown(__instance));
    }

    [HarmonyPatch(typeof(PausePage), "OnShowStart")]
    internal static class PauseShow
    {
        private static void Postfix(PausePage __instance) =>
            Safe.Run("PauseShow", () => Plugin.Instance?.Menu?.PageShown(__instance));
    }

    [HarmonyPatch(typeof(LandingScreenPage), "Start")]
    internal static class LandingPatch
    {
        private static void Postfix(LandingScreenPage __instance) =>
            Safe.Run("Landing", () => Plugin.Instance?.Menu?.LandingShown(__instance));
    }
}
