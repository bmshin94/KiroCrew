"""File shards cover the original suite once without importing sibling files."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path, PurePosixPath, PureWindowsPath

import yaml

from scripts.ci_file_shards import file_shard

ROOT = Path(__file__).resolve().parents[1]


class TestFileShards(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="file-shards-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.write("pytest.ini", "[pytest]\ntestpaths = test apps\n")
        (self.root / "test").mkdir()
        (self.root / "apps").mkdir()
        self.write(
            "conftest.py",
            "import json\n"
            "def pytest_collection_finish(session):\n"
            "    worker = getattr(session.config, 'workerinput', {}).get('workerid', 'main')\n"
            "    path = session.config.rootpath / ('items-' + worker + '.json')\n"
            "    path.write_text(json.dumps([i.nodeid for i in session.items]), encoding='utf-8')\n",
        )

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def run_python(self, *extra):
        env = dict(os.environ)
        env.pop("PYTEST_ADDOPTS", None)
        env.pop("PYTEST_PLUGINS", None)
        env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONPATH"] = str(ROOT)
        env["TMPDIR"] = env["TMP"] = env["TEMP"] = str(self.root)
        return subprocess.run(
            [sys.executable, "-B", *extra],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=45,
        )

    def run_pytest(self, *extra):
        return self.run_python("-m", "pytest", "-p", "scripts.ci_file_shards", "-q", *extra)

    def items(self, worker="main"):
        return json.loads((self.root / f"items-{worker}.json").read_text(encoding="utf-8"))

    def assert_ok(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def seed_suite(self):
        for index in range(18):
            self.write(
                f"{'test/nested' if index % 2 else 'apps/demo'}/test_case_{index}.py",
                "import pytest\n"
                "@pytest.mark.parametrize('value', [0, 1, 2])\n"
                "def test_case(value): assert value >= 0\n",
            )
        # Ignored files would fail at import if discovery or excludes were lost.
        self.write("test/conftest.py", "collect_ignore = ['test_ignored.py']\n")
        for name in ("test/test_ignored.py", "test/data/test_bad.py", "apps/helper.py"):
            self.write(name, "raise RuntimeError('must not import fixture data')\n")
        self.write("test/data/conftest.py", "collect_ignore_glob = ['test_*.py']\n")

    def test_union_matches_unsharded_items_including_parameters_and_ignores(self):
        self.seed_suite()
        self.assert_ok(self.run_pytest("--collect-only"))
        baseline = self.items()
        self.assertEqual(len(baseline), 54)
        all_items = []
        for index in range(1, 4):
            self.assert_ok(self.run_pytest("--file-shards=3", f"--file-shard={index}"))
            shard_items = self.items()
            self.assertTrue(shard_items)
            all_items.extend(shard_items)
        self.assertCountEqual(all_items, baseline)
        self.assertEqual(len(all_items), len(set(all_items)))

    def test_unassigned_file_is_not_imported_even_as_an_explicit_target(self):
        good = self.write("test/test_good.py", "def test_ok(): pass\n")
        owner = file_shard(good, self.root, 2)
        bad = next(
            self.root / f"test/test_bad_{i}.py"
            for i in range(100)
            if file_shard(self.root / f"test/test_bad_{i}.py", self.root, 2) != owner
        )
        self.write(bad.relative_to(self.root), "raise RuntimeError('poison import')\n")
        args = ("--file-shards=2", f"--file-shard={owner}")
        self.assert_ok(self.run_pytest(*args))
        self.assert_ok(self.run_pytest(*args, str(good), str(bad)))
        # Negative control: removing the shard filter exposes the import failure.
        result = self.run_pytest(str(good), str(bad))
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("poison import", result.stdout)

    def test_xdist_workers_collect_identical_shards_and_keep_loadgroup(self):
        self.seed_suite()
        result = self.run_pytest(
            "-p", "xdist.plugin", "-n2", "--dist=loadgroup", "--file-shards=3", "--file-shard=2"
        )
        self.assert_ok(result)
        self.assertEqual(self.items("gw0"), self.items("gw1"))
        self.assertTrue(self.items("gw0"))
        for item in self.items("gw0"):
            self.assertEqual(file_shard(self.root / item.split("::")[0], self.root, 3), 2)

    def test_reduced_scope_is_partitioned_without_expanding_to_other_roots(self):
        files = [self.write(f"test/test_{i}.py", "def test_ok(): pass\n") for i in range(12)]
        self.write("apps/test_outside.py", "raise RuntimeError('outside reduced scope')\n")
        baseline = [f"test/test_{i}.py::test_ok" for i in range(12)]
        actual = []
        for index in (1, 2):
            self.assert_ok(
                self.run_pytest("--file-shards=2", f"--file-shard={index}", *map(str, files))
            )
            actual.extend(self.items())
        self.assertCountEqual(actual, baseline)

    def test_custom_filename_pattern_norecursedirs_and_cli_ignore_survive(self):
        self.write(
            "pytest.ini",
            "[pytest]\ntestpaths = test apps\npython_files = check_*.py\nnorecursedirs = data\n",
        )
        self.write("test/check_ok.py", "def test_ok(): pass\n")
        for name in ("test/test_bad.py", "test/data/check_bad.py", "apps/check_bad.py"):
            self.write(name, "raise RuntimeError('excluded')\n")
        self.assert_ok(
            self.run_pytest("--file-shards=1", "--file-shard=1", "--ignore=apps/check_bad.py")
        )
        self.assertEqual(self.items(), ["test/check_ok.py::test_ok"])

    def test_disabled_plugin_preserves_explicit_node_ids_and_spaced_paths(self):
        target = self.write(
            "test/a space/test_ok.py", "def test_one(): pass\ndef test_two(): assert False\n"
        )
        self.assert_ok(self.run_pytest(str(target) + "::test_one"))
        self.assertEqual(self.items(), ["test/a space/test_ok.py::test_one"])
        self.assert_ok(
            self.run_pytest("--file-shards=1", "--file-shard=1", str(target) + "::test_one")
        )

    def test_invalid_and_empty_shards_fail_instead_of_running_the_full_suite(self):
        self.write("test/test_ok.py", "def test_ok(): pass\n")
        for args in (
            ("--file-shards=0", "--file-shard=1"),
            ("--file-shards=2", "--file-shard=0"),
            ("--file-shards=2", "--file-shard=3"),
            ("--file-shards=2",),
            ("--file-shard=1",),
        ):
            with self.subTest(args=args):
                self.assertEqual(self.run_pytest(*args).returncode, 4)
        owner = file_shard(self.root / "test/test_ok.py", self.root, 2)
        result = self.run_pytest("--file-shards=2", f"--file-shard={3 - owner}")
        self.assertEqual(result.returncode, 5, result.stdout + result.stderr)
        self.assertEqual(self.items(), [])

    def test_owned_import_error_and_test_failure_still_fail(self):
        self.write("test/test_bad.py", "raise RuntimeError('owned import failed')\n")
        self.assertEqual(self.run_pytest("--file-shards=1", "--file-shard=1").returncode, 2)
        self.write("test/test_bad.py", "def test_bad(): assert False\n")
        self.assertEqual(self.run_pytest("--file-shards=1", "--file-shard=1").returncode, 1)

    def test_cannot_apply_item_splitting_on_top_of_file_splitting(self):
        result = self.run_pytest(
            "-p",
            "pytest_split.plugin",
            "--splits=2",
            "--group=1",
            "--file-shards=2",
            "--file-shard=1",
        )
        self.assertEqual(result.returncode, 4, result.stdout + result.stderr)
        self.assertIn("cannot be combined", result.stderr)

    def test_coverage_union_matches_the_unsharded_run(self):
        from coverage import CoverageData

        self.write(
            "logic.py",
            "def choose(value):\n"
            "    if value % 2:\n"
            "        return 'odd'\n"
            "    return 'even'\n",
        )
        self.write("pytest.ini", "[pytest]\ntestpaths = test apps\npythonpath = .\n")
        for i in range(12):
            expected = "odd" if i % 2 else "even"
            self.write(
                f"test/test_cov_{i}.py",
                f"from logic import choose\ndef test_value(): assert choose({i}) == {expected!r}\n",
            )
        args = ("-p", "pytest_cov.plugin", "--cov=logic", "--cov-branch", "--cov-report=")
        self.assert_ok(self.run_pytest(*args))
        (self.root / ".coverage").rename(self.root / "baseline.coverage")
        baseline = CoverageData(basename=str(self.root / "baseline.coverage"))
        baseline.read()
        for index in (1, 2):
            self.assert_ok(self.run_pytest(*args, "--file-shards=2", f"--file-shard={index}"))
            (self.root / ".coverage").rename(self.root / f".coverage.shard{index}")
        self.assert_ok(self.run_python("-m", "coverage", "combine"))
        combined = CoverageData(basename=str(self.root / ".coverage"))
        combined.read()
        self.assertEqual(combined.measured_files(), baseline.measured_files())
        self.assertTrue(baseline.measured_files())
        for filename in baseline.measured_files():
            self.assertEqual(sorted(combined.arcs(filename)), sorted(baseline.arcs(filename)))

    def test_assignment_is_stable_across_checkout_roots_and_path_flavours(self):
        unix_root = PurePosixPath("/checkout")
        win_root = PureWindowsPath("C:/different checkout")
        for i in range(100):
            name = f"test/sub dir/test_{i}.py"
            self.assertEqual(
                file_shard(unix_root / name, unix_root, 8),
                file_shard(win_root / name, win_root, 8),
            )

    def test_workflow_only_changes_linux_and_windows_non_leaf_partitioning(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
        for name in ("backend-test", "backend-test-windows"):
            job = workflow["jobs"][name]
            self.assertEqual(job["env"]["SHARD_COUNT"], 4)
            self.assertEqual(job["strategy"]["matrix"]["group"], list(range(1, 5)))
            command = next(
                s["run"] for s in job["steps"] if s.get("name", "").startswith("Run tests")
            )
            self.assertIn("-p scripts.ci_file_shards", command)
            self.assertIn('--file-shards "$SHARD_COUNT" --file-shard ${{ matrix.group }}', command)
            self.assertNotIn("--splits", command)
            if name == "backend-test":
                leaf = command.split('if [ "$LEAF" = "true" ]; then', 1)[1].split("exit 0", 1)[0]
                self.assertNotIn("ci_file_shards", leaf)
                self.assertIn('"${CHANGED[@]}"', leaf)
                self.assertIn("--cov=kiro_crew --cov=sage_lib", command)
        for name, job in workflow["jobs"].items():
            if name not in ("backend-test", "backend-test-windows"):
                self.assertNotIn("ci_file_shards", json.dumps(job))


if __name__ == "__main__":
    unittest.main()
