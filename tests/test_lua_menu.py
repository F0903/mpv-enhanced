import unittest

from tests.lua_runtime import load, to_lua, to_py

INFO = {
    "choices": [
        {"id": "rtx-high", "title": "RTX High", "settings": {}},
        {"id": "rtx-medium", "title": "RTX Medium", "settings": {}},
    ],
    "options": {
        "resolutions": [
            {"label": "1080p", "width": 1920, "height": 1080},
            {"label": "4K", "width": 4096, "height": 2160},
        ],
        "frame_rates": [24, 30],
        "multipliers": [2, 3],
        "fps_cutoffs": [30, 60],
        "default_fps_cutoff": 60,
    },
    "benchmark_command": ["python"],
}


def target(width=1920, height=1080, fps=30, method="rtx-high", multiplier=2):
    return {
        "max_width": width,
        "max_height": height,
        "max_fps": fps,
        "method": method,
        "multiplier": multiplier,
    }


def raw(*targets, **fields):
    return {
        "enabled": False,
        "default_method": "rtx-high",
        "default_multiplier": 2,
        "fps_cutoff": 60,
        "targets": list(targets),
        **fields,
    }


class DraftTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.draft = load("draft")
        cls.info = to_lua(INFO)

    def normalize(self, value):
        settings, warning = self.draft.normalize(to_lua(value), self.info)
        return settings, warning

    def apply(self, settings, action):
        return self.draft.apply(settings, to_lua(action), self.info)

    def targets(self, settings):
        return to_py(settings["targets"])

    def test_normalize_fills_defaults_for_a_missing_file(self) -> None:
        settings, warning = self.normalize(None)
        self.assertIsNone(warning)
        self.assertFalse(settings["enabled"])
        self.assertEqual(settings["default_method"], "rtx-high")
        self.assertEqual(settings["default_multiplier"], 2)
        self.assertEqual(settings["fps_cutoff"], 60)
        self.assertEqual(self.targets(settings), [])

    def test_normalize_without_choices_defaults_to_none(self) -> None:
        settings, _ = self.draft.normalize(None, to_lua({**INFO, "choices": []}))
        self.assertEqual(settings["default_method"], "none")

    def test_normalize_warns_and_drops_bad_targets(self) -> None:
        settings, warning = self.normalize(
            raw({"max_width": "x"}, target(method="gone"), target())
        )
        self.assertEqual(len(self.targets(settings)), 1)
        self.assertIsNotNone(warning)

    def test_normalize_survives_a_non_table(self) -> None:
        settings, warning = self.normalize("oops")
        self.assertEqual(settings["fps_cutoff"], 60)
        self.assertIsNotNone(warning)

    def test_normalize_replaces_wrongly_typed_fields(self) -> None:
        settings, warning = self.normalize(raw(fps_cutoff="fast", default_method=7))
        self.assertEqual(settings["fps_cutoff"], 60)
        self.assertEqual(settings["default_method"], "rtx-high")
        self.assertIsNotNone(warning)

    def test_normalize_keeps_a_none_target_and_clean_files_have_no_warning(
        self,
    ) -> None:
        settings, warning = self.normalize(raw(target(method="none")))
        self.assertEqual(self.targets(settings)[0]["method"], "none")
        self.assertIsNone(warning)

    def test_identical_limits_are_refused(self) -> None:
        settings, _ = self.normalize(raw(target(), target(4096, 2160)))
        heavy, _light = self.targets(settings)
        self.assertEqual(heavy["max_height"], 2160)
        message = self.apply(
            settings, ["set_target", heavy["id"], "resolution", "1080p"]
        )
        self.assertIn("already", message)
        self.assertEqual(self.targets(settings)[0]["max_height"], 2160)

    def test_edits_resort_the_list_and_keep_ids(self) -> None:
        settings, _ = self.normalize(raw(target(), target(4096, 2160)))
        heavy, light = self.targets(settings)
        self.assertIsNone(
            self.apply(settings, ["set_target", heavy["id"], "max_fps", 24])
        )
        self.assertIsNone(
            self.apply(settings, ["set_target", light["id"], "max_fps", 24])
        )
        ordered = self.targets(settings)
        self.assertEqual([item["id"] for item in ordered], [heavy["id"], light["id"]])

    def test_resolution_edit_moves_a_target_to_the_top(self) -> None:
        settings, _ = self.normalize(raw(target(fps=24), target(1920, 1080, 30)))
        before = self.targets(settings)
        self.assertEqual(before[0]["max_fps"], 30)
        self.assertIsNone(
            self.apply(settings, ["set_target", before[1]["id"], "resolution", "4K"])
        )
        after = self.targets(settings)
        self.assertEqual(after[0]["id"], before[1]["id"])
        self.assertEqual((after[0]["max_width"], after[0]["max_height"]), (4096, 2160))

    def test_change_count_counts_one_edit_as_one(self) -> None:
        saved, _ = self.normalize(raw(target()))
        edited = self.draft.copy(saved)
        self.assertEqual(self.draft.change_count(saved, edited), 0)
        first = self.targets(edited)[0]
        self.apply(edited, ["set_target", first["id"], "method", "rtx-medium"])
        self.assertEqual(self.draft.change_count(saved, edited), 1)
        self.apply(edited, ["set", "fps_cutoff", 30])
        self.assertEqual(self.draft.change_count(saved, edited), 2)

    def test_copy_does_not_share_targets(self) -> None:
        saved, _ = self.normalize(raw(target()))
        edited = self.draft.copy(saved)
        self.apply(
            edited, ["set_target", self.targets(edited)[0]["id"], "method", "none"]
        )
        self.assertEqual(self.targets(saved)[0]["method"], "rtx-high")

    def test_serialize_strips_ids(self) -> None:
        settings, _ = self.normalize(raw(target()))
        serialized = to_py(self.draft.serialize(settings))
        self.assertNotIn("id", serialized["targets"][0])
        self.assertEqual(serialized["fps_cutoff"], 60)
        self.assertFalse(serialized["enabled"])

    def test_add_target_uses_the_first_free_limits(self) -> None:
        settings, _ = self.normalize(raw(target(1920, 1080, 24)))
        self.assertIsNone(self.apply(settings, ["add_target"]))
        limits = {
            (item["max_height"], item["max_fps"]) for item in self.targets(settings)
        }
        self.assertEqual(limits, {(1080, 24), (1080, 30)})
        self.assertEqual(self.targets(settings)[0]["method"], "rtx-high")
        self.assertEqual(self.targets(settings)[0]["multiplier"], 2)

    def test_add_target_reports_when_every_combination_is_used(self) -> None:
        settings, _ = self.normalize(
            raw(
                target(1920, 1080, 24),
                target(1920, 1080, 30),
                target(4096, 2160, 24),
                target(4096, 2160, 30),
            )
        )
        self.assertIn("already", self.apply(settings, ["add_target"]))

    def test_delete_target_removes_it(self) -> None:
        settings, _ = self.normalize(raw(target()))
        first = self.targets(settings)[0]
        self.assertIsNone(self.apply(settings, ["delete_target", first["id"]]))
        self.assertEqual(self.targets(settings), [])

    def test_replace_targets_sorts_and_gives_ids(self) -> None:
        settings, _ = self.normalize(raw())
        self.draft.replace_targets(settings, to_lua([target(), target(4096, 2160)]))
        ordered = self.targets(settings)
        self.assertEqual(ordered[0]["max_height"], 2160)
        self.assertNotEqual(ordered[0]["id"], ordered[1]["id"])


class MenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.draft = load("draft")
        cls.menu = load("menu")

    def build(self, *targets, info=INFO, status=None, edit=None, **fields):
        lua_info = to_lua(info)
        saved, _ = self.draft.normalize(to_lua(raw(*targets, **fields)), lua_info)
        edited = self.draft.copy(saved)
        if edit:
            self.assertIsNone(self.draft.apply(edited, to_lua(edit), lua_info))
        return to_py(
            self.menu.build(saved, edited, lua_info, to_lua(status) if status else None)
        )

    @staticmethod
    def find(menu, title):
        return next(item for item in menu["items"] if item.get("title") == title)

    def test_menu_is_addressed_to_this_script(self) -> None:
        menu = self.build()
        self.assertEqual(menu["type"], "mpv-enhanced")
        self.assertEqual(menu["title"], "MPV Enhanced")
        self.assertEqual(menu["callback"], ["mpv_enhanced", "menu-event"])
        self.assertTrue(menu["keep_open"])

    def test_menu_has_the_approved_rows(self) -> None:
        titles = [item.get("title") for item in self.build()["items"]]
        for title in (
            "Interpolation",
            "Default method",
            "Default multiplier",
            "FPS cutoff",
            "Target overrides",
            "Run benchmark",
            "Apply",
            "Discard changes",
        ):
            self.assertIn(title, titles)

    def test_interpolation_row_shows_its_state_and_toggles(self) -> None:
        self.assertEqual(self.find(self.build(), "Interpolation")["hint"], "Off")
        row = self.find(self.build(enabled=True), "Interpolation")
        self.assertEqual(row["hint"], "On")
        self.assertEqual(row["value"], ["toggle"])

    def test_defaults_are_muted_only_while_targets_exist(self) -> None:
        self.assertFalse(self.find(self.build(), "Default method").get("muted", False))
        with_target = self.build(target())
        self.assertTrue(self.find(with_target, "Default method")["muted"])
        self.assertTrue(self.find(with_target, "Default multiplier")["muted"])
        self.assertIn(
            "Only used while there are no target overrides",
            [item.get("title") for item in with_target["items"]],
        )

    def test_apply_shows_pending_count_and_is_disabled_at_zero(self) -> None:
        clean = self.find(self.build(), "Apply")
        self.assertFalse(clean["selectable"])
        changed = self.find(self.build(edit=["set", "fps_cutoff", 30]), "Apply")
        self.assertEqual(changed["hint"], "1 change")
        self.assertTrue(changed["selectable"])
        self.assertEqual(changed["value"], ["apply"])

    def test_method_list_has_installed_choices_and_none(self) -> None:
        methods = self.find(self.build(), "Default method")["items"]
        titles = [item["title"] for item in methods]
        self.assertEqual(titles, ["RTX High", "RTX Medium", "None"])
        self.assertTrue(methods[0]["active"])
        self.assertEqual(methods[1]["value"], ["set", "default_method", "rtx-medium"])

    def test_fps_cutoff_list_includes_off(self) -> None:
        items = self.find(self.build(), "FPS cutoff")["items"]
        self.assertEqual([item["title"] for item in items], ["30 fps", "60 fps", "Off"])
        self.assertEqual(items[2]["value"], ["set", "fps_cutoff", 0])
        self.assertTrue(items[1]["active"])

    def test_none_greys_out_the_multiplier(self) -> None:
        row = self.find(self.build(default_method="none"), "Default multiplier")
        self.assertFalse(row["selectable"])
        self.assertTrue(row["muted"])

    def test_no_methods_hides_interpolation_rows(self) -> None:
        menu = self.build(info={**INFO, "choices": []})
        titles = [item.get("title") for item in menu["items"]]
        self.assertNotIn("Interpolation", titles)
        self.assertNotIn("Target overrides", titles)
        self.assertNotIn("Run benchmark", titles)
        self.assertEqual(len(titles), 1)

    def test_idle_menu_has_no_status_row(self) -> None:
        titles = [item.get("title") for item in self.build(target())["items"]]
        self.assertFalse(any(str(title).startswith("Playing") for title in titles))

    def test_status_rows_are_not_selectable(self) -> None:
        menu = self.build(status={"source": "Playing: 4K", "using": "Using: RTX High"})
        for title in ("Playing: 4K", "Using: RTX High"):
            self.assertFalse(self.find(menu, title)["selectable"])

    def test_target_rows_are_sorted_and_mark_the_current_one(self) -> None:
        lua_info = to_lua(INFO)
        saved, _ = self.draft.normalize(
            to_lua(raw(target(), target(4096, 2160, 30, "rtx-medium"))), lua_info
        )
        ids = [item["id"] for item in to_py(saved["targets"])]
        menu = to_py(
            self.menu.build(
                saved,
                self.draft.copy(saved),
                lua_info,
                to_lua({"source": "a", "using": "b", "target_id": ids[0]}),
            )
        )
        rows = self.find(menu, "Target overrides")
        self.assertEqual(rows["hint"], "2")
        self.assertEqual(rows["items"][0]["title"], "● Up to 4K · 30 fps")
        self.assertEqual(rows["items"][0]["hint"], "RTX Medium ×2")
        self.assertEqual(rows["items"][1]["title"], "Up to 1080p · 30 fps")
        self.assertEqual(rows["items"][2]["title"], "Add target")
        self.assertEqual(rows["items"][2]["value"], ["add_target"])

    def test_none_target_hint_and_its_submenu(self) -> None:
        menu = self.build(target(method="none"))
        row = self.find(menu, "Target overrides")["items"][0]
        self.assertEqual(row["hint"], "None")
        multiplier = next(i for i in row["items"] if i.get("title") == "Multiplier")
        self.assertFalse(multiplier["selectable"])

    def test_target_submenu_has_its_rows_and_stable_id(self) -> None:
        row = self.find(self.build(target()), "Target overrides")["items"][0]
        self.assertTrue(row["id"])
        titles = [item.get("title") for item in row["items"]]
        for title in (
            "Max resolution",
            "Max frame rate",
            "Method",
            "Multiplier",
            "Delete target",
        ):
            self.assertIn(title, titles)
        resolution = next(i for i in row["items"] if i["title"] == "Max resolution")
        self.assertEqual(resolution["hint"], "1080p")
        self.assertEqual(
            resolution["items"][1]["value"],
            ["set_target", row["id"], "resolution", "4K"],
        )


if __name__ == "__main__":
    unittest.main()
