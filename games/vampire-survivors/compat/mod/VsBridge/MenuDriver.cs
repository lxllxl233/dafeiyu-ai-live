using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;
using UnityEngine;
using VampireSurvivors;
using VampireSurvivors.Data;
using VampireSurvivors.Objects.Characters;
using VampireSurvivors.UI;

namespace VsBridge
{
    /// <summary>
    /// Drives the game's own menu methods from Update() timers (no coroutines:
    /// IL2CPP's stripped MonoBehaviour only exposes the string overload).
    /// Python only has to move and optionally pick a level-up; everything else
    /// is advanced here so a run can start without any keyboard input.
    /// </summary>
    public sealed class MenuDriver
    {
        private const float ChoiceTimeout = 8f;

        private sealed class Pending
        {
            public BaseUIPage Page;
            public float Due;
            public Action Action;
        }

        private readonly Dictionary<BaseUIPage, float> _shownAt =
            new Dictionary<BaseUIPage, float>();
        private readonly HashSet<BaseUIPage> _started = new HashSet<BaseUIPage>();
        private readonly HashSet<BaseUIPage> _active = new HashSet<BaseUIPage>();
        private readonly HashSet<BaseUIPage> _nudged = new HashSet<BaseUIPage>();
        private readonly List<Pending> _pending = new List<Pending>();
        private BaseUIPage _current;
        private LandingScreenPage _landing;
        private LandingScreenPage _landingDoneFor;
        private float _landingDue;
        private LevelUpPage _levelPage;
        private float _levelDeadline;
        private volatile int _choice = -1;

        private static readonly HashSet<Type> Handled = new HashSet<Type>
        {
            typeof(WarningPage), typeof(MainMenuPage), typeof(CharacterSelectionPage),
            typeof(WeaponSelectionPage), typeof(StageSelectPage), typeof(MainGamePage),
            typeof(LevelUpPage), typeof(ArcanaMainSelectionPage), typeof(OpenTreasurePage),
            typeof(ItemFoundPage), typeof(CharacterFoundPage), typeof(GameOverPage),
            typeof(RecapPage), typeof(PausePage),
        };

        private static readonly HashSet<Type> Ignored = new HashSet<Type>
        {
            typeof(BackgroundPage), typeof(MenuBannerPage),
        };

        public MenuDriver(BridgeBehaviour behaviour) { _ = behaviour; }

        public string Kind { get; private set; } = "";
        public bool LevelUpActive => _levelPage != null;

        /// <summary>
        /// Reconciles pages that were already active before the plugin loaded
        /// (landing, warning, main menu) or that Unity destroyed without a hide hook.
        /// </summary>
        public void ScanExisting()
        {
            try
            {
                var seen = new HashSet<BaseUIPage>();
                foreach (var page in UnityEngine.Object.FindObjectsOfType<BaseUIPage>())
                {
                    seen.Add(page);
                    if (!Handled.Contains(page.GetType())) continue;
                    if (page is BackgroundPage || page is MenuBannerPage) continue;
                    PageShown(page);
                }
                _active.RemoveWhere(p => !seen.Contains(p));
                var landing = UnityEngine.Object.FindObjectOfType<LandingScreenPage>();
                if (landing != null)
                {
                    if (landing != _landingDoneFor)
                    {
                        _landing = landing;
                        if (Plugin.Automation) TryLanding(landing);
                    }
                }
                else
                {
                    _landing = null;
                    _landingDoneFor = null;
                }
            }
            catch (Exception ex)
            {
                Plugin.LogSrc?.LogWarning($"page scan failed: {ex.Message}");
            }
        }

        public void PageShown(BaseUIPage page)
        {
            if (page == null) return;
            _landing = null;
            _active.Add(page);
            _shownAt[page] = Time.realtimeSinceStartup;
            _nudged.Remove(page);
            _current = page;
            Kind = KindOf(page);
            if (page is LevelUpPage level && _levelPage != level) LevelUpReady(level);
            if (Plugin.Automation) Advance(page);
        }

        public void PageHidden(BaseUIPage page)
        {
            if (page == null) return;
            _active.Remove(page);
            _shownAt.Remove(page);
            _started.Remove(page);
            _nudged.Remove(page);
            if (_current == page)
            {
                _current = null;
                Kind = "";
            }
            if (_levelPage == page)
            {
                _levelPage = null;
                _choice = -1;
            }
        }

        public void LandingShown(LandingScreenPage page)
        {
            if (_landing == page) return;
            _landing = page;
            // Give the page's Zenject/UI one beat before asking it to advance;
            // calling MoveToNextView inside Start() throws a NullReferenceException.
            _landingDue = Time.realtimeSinceStartup + 1.2f;
        }

        public void LandingHidden(LandingScreenPage page)
        {
            if (_landing == page) _landing = null;
        }

        private void TryLanding(LandingScreenPage page)
        {
            if (page == null || _landing != page) return;
            try
            {
                page.MoveToNextView();
                _landing = null;
                _landingDoneFor = page;
            }
            catch (Exception ex)
            {
                // Zenject may not be ready for the first few frames; retry.
                _landingDue = Time.realtimeSinceStartup + 0.6f;
                Plugin.LogSrc?.LogWarning($"landing failed: {ex.Message}");
            }
        }

        public void LevelUpReady(LevelUpPage page)
        {
            _levelPage = page;
            _choice = -1;
            _levelDeadline = Time.realtimeSinceStartup + ChoiceTimeout;
        }

        public void SetChoice(int index)
        {
            if (index >= 0) _choice = index;
        }

        public void Press(string key)
        {
            if (string.IsNullOrEmpty(key)) return;
            if (key.Length == 1 && key[0] >= '1' && key[0] <= '9')
            {
                SetChoice(key[0] - '1');
                return;
            }
            if ((key == "enter" || key == "space") && _current != null)
            {
                try { _current.OnEnterPressed(); }
                catch (Exception ex)
                { Plugin.LogSrc?.LogWarning($"enter on menu failed: {ex.Message}"); }
            }
        }

        public void RequestStart()
        {
            if (_current != null && _active.Contains(_current)) Advance(_current);
            else if (_landing != null)
                _landingDue = Time.realtimeSinceStartup + 0.3f;
        }

        public void Cancel()
        {
            _choice = -1;
            _pending.Clear();
        }

        public JToken Json()
        {
            var page = _levelPage ?? _current;
            if (page == null || !_active.Contains(page)) return null;
            var obj = new JObject
            {
                ["kind"] = Kind,
                ["title"] = page.GetType().Name,
                ["options"] = Options(page),
            };
            return obj;
        }

        /// <summary>Main-thread timer pump, called from BridgeBehaviour.Update.</summary>
        public void Update()
        {
            float now = Time.realtimeSinceStartup;

            if (_landing != null && now >= _landingDue)
                TryLanding(_landing);

            for (int i = _pending.Count - 1; i >= 0; i--)
            {
                var p = _pending[i];
                if (now < p.Due) continue;
                _pending.RemoveAt(i);
                if (p.Page != null && !_active.Contains(p.Page)) continue;
                try { p.Action?.Invoke(); }
                catch (Exception ex) { Plugin.LogSrc?.LogWarning($"menu action failed: {ex.Message}"); }
            }

            if (_levelPage != null)
            {
                var page = _levelPage;
                if (!_active.Contains(page))
                {
                    _levelPage = null;
                    _choice = -1;
                }
                else if (_choice >= 0 || now >= _levelDeadline)
                {
                    SelectLevel(page);
                }
            }

            if (!Plugin.Automation || _shownAt.Count == 0) return;
            foreach (var kv in _shownAt)
            {
                var page = kv.Key;
                if (page == null || Handled.Contains(page.GetType())
                    || Ignored.Contains(page.GetType()) || _nudged.Contains(page)) continue;
                if (now - kv.Value < 10f || !page.gameObject.activeInHierarchy) continue;
                _nudged.Add(page);
                try { page.OnEnterPressed(); }
                catch (Exception ex)
                { Plugin.LogSrc?.LogWarning($"unknown page enter failed: {ex.Message}"); }
                break;
            }
        }

        // ------------------------------------------------------------------ scheduling
        private void Schedule(BaseUIPage page, float delay, Action action)
        {
            _pending.Add(new Pending
            {
                Page = page,
                Due = Time.realtimeSinceStartup + Mathf.Max(0.01f, delay),
                Action = action,
            });
        }

        private void Advance(BaseUIPage page)
        {
            if (page == null || !Plugin.Automation || _started.Contains(page)) return;
            _started.Add(page);
            switch (page)
            {
                case WarningPage warning:
                    Schedule(page, 1.0f, () => warning.Complete());
                    break;
                case MainMenuPage menu:
                    Schedule(page, 1.0f, () => menu.ShowCharacterSelect());
                    break;
                case CharacterSelectionPage select:
                    Schedule(page, 0.8f, () => CharacterSelect(select));
                    break;
                case WeaponSelectionPage weapon:
                    Schedule(page, 0.6f, () => weapon.Skip());
                    break;
                case StageSelectPage stage:
                    Schedule(page, 0.8f, () => StageSelect(stage));
                    break;
                case LevelUpPage level:
                    LevelUpReady(level);
                    break;
                case ArcanaMainSelectionPage arcana:
                    Schedule(page, 0.8f, () => Arcana(arcana));
                    break;
                case OpenTreasurePage treasure:
                    Schedule(page, 0.5f, () => TreasureTick(treasure));
                    break;
                case ItemFoundPage item:
                    Schedule(page, 0.8f, () => item.Receive());
                    break;
                case CharacterFoundPage character:
                    Schedule(page, 0.8f, () => character.Reveal());
                    Schedule(page, 2.3f, () => character.CollectCharacter());
                    break;
                case GameOverPage over:
                    Schedule(page, 1.5f, () => over.Quit());
                    break;
                case RecapPage recap:
                    Schedule(page, 0.8f, () => recap.DoneClicked());
                    break;
                case PausePage pause:
                    Schedule(page, 0.5f, () => pause.ReturnToGame());
                    break;
            }
        }

        // ------------------------------------------------------------------ pages
        private void CharacterSelect(CharacterSelectionPage page)
        {
            if (page == null || !page.gameObject.activeInHierarchy) return;
            CharacterItemUI pick = null;
            foreach (var kv in page._characterItemUIs)
            {
                var ui = kv.Value;
                if (ui == null || !ui.IsAvailable()) continue;
                pick = ui;
                break;
            }
            if (pick == null) return;
            var data = pick.CharacterItem.CharacterData;
            try
            {
                page.ShowCharacterInfo(data, pick.Type, pick);
                page.SelectCharacter(false);
                Schedule(page, 0.2f, () => page.ConfirmCharacter());
            }
            catch (Exception ex)
            {
                Plugin.LogSrc?.LogWarning($"character select failed: {ex.Message}");
            }
        }

        private void StageSelect(StageSelectPage page)
        {
            if (page == null || !page.gameObject.activeInHierarchy) return;
            StageItemUI pick = null;
            foreach (var go in page._spawned)
            {
                var item = go != null ? go.GetComponent<StageItemUI>() : null;
                var data = item != null ? item.GetData() : null;
                if (item == null || data == null || !data.unlocked) continue;
                pick = item;
                break;
            }
            if (pick == null) return;
            try
            {
                page.SetInfoPanel(pick, pick.GetData(), pick.Type);
                page.SelectStage();
                Schedule(page, 0.2f, () =>
                {
                    page._HyperModeTickBox?.InitialSet(false);
                    page._HurryModeTickBox?.InitialSet(false);
                    page._MazzoModeTickBox?.InitialSet(false);
                    page._LimitBreakTickBox?.InitialSet(false);
                    page._InverseModeTickBox?.InitialSet(false);
                    page._EndlessModeTickBox?.InitialSet(false);
                    var cfg = page._playerOptions?.Config;
                    if (cfg != null)
                    {
                        cfg.SelectedLimitBreak = false;
                        cfg.SelectedInverse = false;
                        cfg.SelectedReapers = false;
                    }
                    page.ConfirmStage();
                });
            }
            catch (Exception ex)
            {
                Plugin.LogSrc?.LogWarning($"stage select failed: {ex.Message}");
            }
        }

        private void SelectLevel(LevelUpPage page)
        {
            try
            {
                var items = page.LevelUpItems;
                if (items == null || items.Count == 0)
                {
                    page.Skip();
                }
                else
                {
                    int idx = Mathf.Clamp(_choice, 0, items.Count - 1);
                    items[idx].Select();
                }
            }
            catch (Exception ex)
            {
                Plugin.LogSrc?.LogWarning($"level up select failed: {ex.Message}");
            }
            finally
            {
                if (_current == page)
                {
                    _active.Remove(page);
                    _current = null;
                    Kind = "";
                }
                _levelPage = null;
                _choice = -1;
            }
        }

        private void Arcana(ArcanaMainSelectionPage page)
        {
            if (page == null || !page.gameObject.activeInHierarchy) return;
            try
            {
                if (page._unlockedCards != null && page._unlockedCards.Count > 0)
                {
                    var card = page._unlockedCards[0];
                    if (card != null)
                    {
                        page.SetInfo(card.GetData(), card.GetArcanaType(), card);
                        page.Select();
                        return;
                    }
                }
                foreach (var go in page._spawned)
                {
                    var card = go != null ? go.GetComponent<ArcanaCardUI>() : null;
                    var data = card != null ? card.GetData() : null;
                    if (data == null || !data.unlocked) continue;
                    page.SetInfo(data, card.GetArcanaType(), card);
                    page.Select();
                    return;
                }
                page.Skip();
            }
            catch (Exception ex)
            {
                Plugin.LogSrc?.LogWarning($"arcana failed: {ex.Message}");
            }
        }

        private void TreasureTick(OpenTreasurePage page)
        {
            if (page == null || !page.gameObject.activeInHierarchy) return;
            try
            {
                if (!page._openButtonPressed) page.OpenTreasure();
                else if (page._isPlaying && page._canSkip
                         && page._animCanBeSkippedPastThisPoint && !page._isSkipped)
                    page.Skip();
                if (page.DoneButton != null && page.DoneButton.activeInHierarchy
                    && !page._doneButtonPressed)
                {
                    page.ClaimTreasure();
                    return;
                }
            }
            catch (Exception ex)
            {
                Plugin.LogSrc?.LogWarning($"treasure failed: {ex.Message}");
                return;
            }
            Schedule(page, 0.25f, () => TreasureTick(page));
        }

        // ------------------------------------------------------------------ kinds/options
        private static string KindOf(BaseUIPage page)
        {
            if (page is LevelUpPage) return "level_up";
            if (page is GameOverPage || page is RecapPage) return "game_over";
            if (page is PausePage) return "paused";
            if (page is MainGamePage) return "";
            return "menu";
        }

        private static JArray Options(BaseUIPage page)
        {
            var arr = new JArray();
            try
            {
                if (page is LevelUpPage level)
                {
                    foreach (var ui in level.LevelUpItems)
                    {
                        if (ui == null) continue;
                        string kind = ui._isLimitBreak ? "limit_break"
                            : ui.IsWeapon() ? "weapon"
                            : ui.IsPowerUp() ? "passive" : "item";
                        string id = ui._type != WeaponType.VOID
                            ? ui._type.ToString() : ui._itemType.ToString();
                        string name = ui._data?.name ?? ui._itemData?.name ?? id;
                        arr.Add(new JObject
                        {
                            ["key"] = (arr.Count + 1).ToString(),
                            ["id"] = id,
                            ["name"] = name,
                            ["kind"] = kind,
                            ["level"] = ui._levelData?.level ?? 1,
                            ["is_new"] = ui.IsNew(),
                        });
                    }
                }
                else if (page is CharacterSelectionPage chars)
                {
                    foreach (var kv in chars._characterItemUIs)
                    {
                        var ui = kv.Value;
                        if (ui == null || !ui.IsAvailable()) continue;
                        var data = ui.CharacterItem.CharacterData;
                        arr.Add(new JObject
                        {
                            ["key"] = (arr.Count + 1).ToString(),
                            ["id"] = kv.Key.ToString(),
                            ["name"] = $"{data.charName} {data.surname}".Trim(),
                        });
                    }
                }
                else if (page is StageSelectPage stages)
                {
                    foreach (var go in stages._spawned)
                    {
                        var item = go != null ? go.GetComponent<StageItemUI>() : null;
                        var data = item != null ? item.GetData() : null;
                        if (data == null || !data.unlocked) continue;
                        arr.Add(new JObject
                        {
                            ["key"] = (arr.Count + 1).ToString(),
                            ["id"] = item.Type.ToString(),
                            ["name"] = data.stageName ?? item.Type.ToString(),
                        });
                    }
                }
            }
            catch (Exception ex)
            {
                Plugin.LogSrc?.LogWarning($"menu options failed: {ex.Message}");
            }
            return arr;
        }
    }
}
