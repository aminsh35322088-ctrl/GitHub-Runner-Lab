from pathlib import Path
import sys
import unittest
from unittest import mock
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
import lab_tools

class ToolCatalogTests(unittest.TestCase):
    def setUp(self):
        self.catalog=lab_tools.load_catalog(ROOT/"config/runner-tools.json")
    def test_catalog_ids_and_categories(self):
        ids=[x["id"] for x in self.catalog["tools"]]
        self.assertEqual(len(ids),len(set(ids)))
        known={x["id"] for x in self.catalog["categories"]}
        self.assertTrue(all(x["category"] in known for x in self.catalog["tools"]))
        self.assertGreaterEqual(len(ids),50)
    def test_intent_search(self):
        self.assertEqual(lab_tools.search_tools(self.catalog,"inspect APK dex decompile")[0]["id"],"jadx")
        self.assertEqual(lab_tools.search_tools(self.catalog,"packet loss mtr route")[0]["id"],"mtr")
        self.assertEqual(lab_tools.search_tools(self.catalog,"convert video")[0]["id"],"ffmpeg")
        self.assertEqual(lab_tools.search_tools(self.catalog,"shfmt")[0]["id"],"shfmt")

    def test_manifest_commands_are_discoverable(self):
        import json
        manifest = json.loads((ROOT / "config/runner-toolset.json").read_text())
        catalog = lab_tools.load_catalog(ROOT / "config/runner-tools.json")
        catalog_commands = {command for tool in catalog["tools"] for command in tool.get("commands", [])}
        configured_commands = {command for group in manifest["commands"].values() for command in group}
        self.assertEqual(configured_commands - catalog_commands, set())

    def test_empty_and_unknown_search(self):
        self.assertEqual(lab_tools.search_tools(self.catalog,""),[])
        self.assertEqual(lab_tools.search_tools(self.catalog,"zzzxxyy-unknown-capability"),[])
    def test_status_checks_all_commands(self):
        with mock.patch.object(lab_tools.shutil,"which",side_effect=lambda x:"/usr/bin/present" if x=="present" else None):
            result=lab_tools.tool_status({"id":"x","commands":["present","missing"]})
        self.assertEqual(result["status"],"MISSING")
        self.assertEqual(result["missing_commands"],["missing"])
    def test_provider_status_is_shallow_by_default(self):
        with mock.patch.object(lab_tools.shutil,"which",return_value="/usr/bin/rea"):
            result=lab_tools.tool_status({"id":"ghidra","provider":"ghidra"})
        self.assertEqual(result["status"],"PROVIDER")
        self.assertIn("rea doctor",result["reason"])
    def test_index_and_onboarding(self):
        self.assertIn("agent-lab tools search",lab_tools.index_text(self.catalog))
        summary=lab_tools.onboarding_summary()
        self.assertEqual(summary["search"],"agent-lab tools search QUERY")
        self.assertGreaterEqual(sum(len(x["tools"]) for x in summary["categories"]),50)
    def test_router_and_prewarmer(self):
        router=(ROOT/"scripts/agent-run.sh").read_text()
        prewarm=(ROOT/"scripts/agent-prewarm.sh").read_text()
        bootstrap=(ROOT/"scripts/agent-bootstrap.sh").read_text()
        lib=(ROOT/"scripts/agent-lib.sh").read_text()
        self.assertIn('tools) exec python3 "$SELF_DIR/lab_tools.py" "$@" ;;',router)
        self.assertIn("registry/TOOL-INDEX.md",prewarm)
        self.assertIn("tools/reverse-engineering/rea",prewarm)
        self.assertIn("/opt/runner-tools",bootstrap)
        self.assertIn("RUNNER_TOOLS_ROOT/bin",lib)
    def test_documentation_teaches_catalog_first(self):
        self.assertIn("Runner Tool Center",(ROOT/"AGENTS.md").read_text())
        self.assertIn("Runner Tool Center",(ROOT/"README.md").read_text())

if __name__=="__main__": unittest.main()
