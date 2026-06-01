"""Packaging tests for the mdhumanviewer plugin.

Asserts the two .claude-plugin manifests parse, that plugin.json names the
plugin "mdhumanviewer", and that the own marketplace entry points back at the
plugin (name matches, source is "./"). plugin.json is the single source of
truth for VERSION (the entry must not redeclare it); the marketplace entry adds
listing/discovery metadata (display name, blurb, category).

Run ONLY this module from the plugin root:
    python3 -m unittest tests.test_packaging -v
"""
import json
import os
import unittest


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN_JSON = os.path.join(REPO_ROOT, ".claude-plugin", "plugin.json")
MARKETPLACE_JSON = os.path.join(REPO_ROOT, ".claude-plugin", "marketplace.json")

PLUGIN_NAME = "mdhumanviewer"


def load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


class TestPluginJson(unittest.TestCase):
    def test_file_exists(self):
        self.assertTrue(os.path.isfile(PLUGIN_JSON), f"missing {PLUGIN_JSON}")

    def test_parses(self):
        data = load(PLUGIN_JSON)
        self.assertIsInstance(data, dict)

    def test_name_is_mdhumanviewer(self):
        data = load(PLUGIN_JSON)
        self.assertEqual(data["name"], PLUGIN_NAME)

    def test_display_name(self):
        data = load(PLUGIN_JSON)
        self.assertEqual(data["displayName"], "mdHumanViewer")

    def test_version(self):
        data = load(PLUGIN_JSON)
        self.assertEqual(data["version"], "0.2.2")

    def test_author(self):
        data = load(PLUGIN_JSON)
        self.assertEqual(data["author"]["name"], "Kirill")
        self.assertEqual(data["author"]["email"], "hi@werko.dev")
        self.assertEqual(data["author"]["url"], "https://werko.dev")

    def test_license(self):
        data = load(PLUGIN_JSON)
        self.assertEqual(data["license"], "MIT")

    def test_keywords(self):
        data = load(PLUGIN_JSON)
        expected = {
            "markdown", "html", "overview", "documentation",
            "system-map", "onboarding", "audit", "parallel",
        }
        self.assertEqual(set(data["keywords"]), expected)

    def test_homepage_and_repository_point_at_new_repo(self):
        data = load(PLUGIN_JSON)
        self.assertEqual(
            data["homepage"], "https://github.com/werkodev/mdhumanviewer")
        self.assertEqual(
            data["repository"], "https://github.com/werkodev/mdhumanviewer.git")

    def test_description_summarizes_pipeline_and_guarantee(self):
        data = load(PLUGIN_JSON)
        desc = data["description"]
        # S0-S5 pipeline mentioned.
        for stage in ("S0", "S5"):
            self.assertIn(stage, desc)
        # read-once / fully-parallel guarantee mentioned.
        self.assertIn("parallel", desc.lower())


class TestMarketplaceJson(unittest.TestCase):
    def test_file_exists(self):
        self.assertTrue(
            os.path.isfile(MARKETPLACE_JSON), f"missing {MARKETPLACE_JSON}")

    def test_parses(self):
        data = load(MARKETPLACE_JSON)
        self.assertIsInstance(data, dict)

    def test_marketplace_name_is_single_plugin_catalog(self):
        # A one-plugin catalog named after its plugin (the marketplace name and
        # the plugin name share the "mdhumanviewer" slug, different scopes:
        # users install mdhumanviewer@mdhumanviewer).
        data = load(MARKETPLACE_JSON)
        self.assertEqual(data["name"], PLUGIN_NAME)

    def test_owner_is_werkodev(self):
        data = load(MARKETPLACE_JSON)
        self.assertEqual(data["owner"]["name"], "werkodev")
        self.assertEqual(data["owner"]["email"], "hi@werko.dev")
        self.assertEqual(data["owner"]["url"], "https://werko.dev")

    def test_entry_points_at_plugin(self):
        data = load(MARKETPLACE_JSON)
        plugins = data["plugins"]
        self.assertIsInstance(plugins, list)
        self.assertEqual(len(plugins), 1)
        entry = plugins[0]
        # Name matches the plugin and source is the repo root.
        self.assertEqual(entry["name"], PLUGIN_NAME)
        self.assertEqual(entry["source"], "./")

    def test_entry_omits_version(self):
        # plugin.json is the single source of truth for VERSION; the entry must
        # NOT redeclare it (if it did, plugin.json silently wins — confusing).
        data = load(MARKETPLACE_JSON)
        entry = data["plugins"][0]
        self.assertNotIn("version", entry)

    def test_entry_carries_listing_metadata(self):
        # The marketplace entry owns the LISTING / DISCOVERY surface: a concise
        # blurb + a category + a stylized display name (distinct from the full
        # manifest description in plugin.json). Keep them present and non-empty.
        data = load(MARKETPLACE_JSON)
        entry = data["plugins"][0]
        self.assertTrue(entry.get("description"), "entry needs a listing description")
        self.assertEqual(entry.get("category"), "documentation")
        self.assertEqual(entry.get("displayName"), "mdHumanViewer")


class TestCrossConsistency(unittest.TestCase):
    def test_marketplace_entry_name_matches_plugin_name(self):
        plugin = load(PLUGIN_JSON)
        marketplace = load(MARKETPLACE_JSON)
        entry_names = {p["name"] for p in marketplace["plugins"]}
        self.assertIn(plugin["name"], entry_names)


if __name__ == "__main__":
    unittest.main()
