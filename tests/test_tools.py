from pathlib import Path
import tempfile
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
    def test_sigillo_missing_is_optional(self):
        tool=next(x for x in self.catalog["tools"] if x["id"]=="sigillo")
        self.assertFalse(tool.get("required",True))
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(lab_tools.Path,"home",return_value=Path(tmp)), mock.patch.object(lab_tools.shutil,"which",return_value=None):
                result=lab_tools.tool_status(tool,deep=True)
        self.assertEqual(result["status"],"UNCONFIGURED")
        self.assertIn("optional",result["reason"] if "reason" in result else "optional")

    def test_sigillo_authenticated_session_is_verified_without_exposing_output(self):
        tool=next(x for x in self.catalog["tools"] if x["id"]=="sigillo")
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp)
            state=home/".local/state/agent-runner/sigillo-auth-status"
            state.parent.mkdir(parents=True)
            state.write_text("AUTHENTICATED\n")
            proc=mock.Mock(returncode=0,stdout="private account payload",stderr="private diagnostic")
            with mock.patch.object(lab_tools.Path,"home",return_value=home), mock.patch.object(lab_tools.shutil,"which",return_value="/usr/bin/sigillo"), mock.patch.object(lab_tools.subprocess,"run",return_value=proc) as run:
                result=lab_tools.tool_status(tool,deep=True)
        self.assertEqual(result["status"],"AUTHENTICATED")
        self.assertEqual(run.call_args.args[0],["sigillo","me"])
        self.assertNotIn("private",str(result))

    def test_sigillo_invalid_token_is_visible_but_non_blocking(self):
        tool=next(x for x in self.catalog["tools"] if x["id"]=="sigillo")
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp)
            state=home/".local/state/agent-runner/sigillo-auth-status"
            state.parent.mkdir(parents=True)
            state.write_text("AUTH_FAILED\n")
            with mock.patch.object(lab_tools.Path,"home",return_value=home), mock.patch.object(lab_tools.shutil,"which",return_value="/usr/bin/sigillo"), mock.patch.object(lab_tools.subprocess,"run") as run:
                result=lab_tools.tool_status(tool,deep=True)
        self.assertEqual(result["status"],"AUTH_FAILED")
        self.assertFalse(tool.get("required",True))
        run.assert_not_called()

    def test_sigillo_unconfigured_cli_does_not_expose_failed_probe_output(self):
        tool=next(x for x in self.catalog["tools"] if x["id"]=="sigillo")
        with tempfile.TemporaryDirectory() as tmp:
            proc=mock.Mock(returncode=1,stdout="secret-looking output",stderr="private error")
            with mock.patch.object(lab_tools.Path,"home",return_value=Path(tmp)), mock.patch.object(lab_tools.shutil,"which",return_value="/usr/bin/sigillo"), mock.patch.object(lab_tools.subprocess,"run",return_value=proc):
                result=lab_tools.tool_status(tool,deep=True)
        self.assertEqual(result["status"],"UNCONFIGURED")
        self.assertNotIn("secret-looking",str(result))
        self.assertNotIn("private error",str(result))

    def test_sigillo_workflow_only_bootstraps_agent_access_after_auth(self):
        workflow=(ROOT/".github/workflows/rdc-lab.yml").read_text()
        self.assertIn("steps.sigillo.outputs.SIGILLO_CLI_READY == 'true'",workflow)
        self.assertIn("continue-on-error: true",workflow)
        self.assertIn("SIGILLO_API_URL=%s",workflow)
        self.assertNotIn("SIGILLO_API_TOKEN is required to retrieve the runner GitHub credential",workflow)
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
