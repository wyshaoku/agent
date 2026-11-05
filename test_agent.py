import builtins
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import agent


class OllamaMessageConstructionTests(unittest.TestCase):
    def test_merges_duplicate_roles_filters_system_and_tool_and_does_not_mutate_history(self):
        history = [
            {"role": "system", "content": "system rules"},
            {"role": "user", "content": "initial task"},
            {"role": "user", "content": "state context"},
            {"role": "assistant", "content": "first assistant"},
            {"role": "assistant", "content": "second assistant"},
            {"role": "user", "content": "current task"},
            {"role": "tool", "content": "ignored tool record"},
            {"role": "assistant", "content": "latest answer"},
        ]
        original = [dict(item) for item in history]

        request = agent.build_ollama_messages(history)

        self.assertEqual([item["role"] for item in request], ["user", "assistant", "user", "assistant", "user"])
        self.assertTrue(request[0]["content"].startswith("system rules"))
        self.assertIn("initial task", request[0]["content"])
        self.assertIn("state context", request[0]["content"])
        self.assertIn("first assistant", request[1]["content"])
        self.assertIn("second assistant", request[1]["content"])
        self.assertNotIn("ignored tool record", " ".join(item["content"] for item in request))
        self.assertEqual(history, original)

    def test_long_merged_user_turn_keeps_initial_and_latest_content(self):
        history = [
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "INITIAL TASK " + "a" * 3500},
            {"role": "user", "content": "b" * 3500 + " LATEST TASK"},
        ]

        request = agent.build_ollama_messages(history)

        self.assertEqual([item["role"] for item in request], ["user"])
        self.assertLessEqual(len(request[0]["content"]), 6000)
        self.assertIn("INITIAL TASK", request[0]["content"])
        self.assertIn("LATEST TASK", request[0]["content"])

    def test_trimming_at_alternating_window_boundary_restores_user_start(self):
        history = [{"role": "system", "content": "rules"}]
        history.extend(
            {"role": "user" if index % 2 == 0 else "assistant", "content": f"turn {index}"}
            for index in range(13)
        )

        request = agent.build_ollama_messages(history)

        self.assertEqual(request[0]["role"], "user")
        self.assertLessEqual(len(request), 13)
        self.assertTrue(all(item["role"] == ("user" if i % 2 == 0 else "assistant") for i, item in enumerate(request)))
        self.assertEqual(request[-1]["role"], "user")


class RecoveryFlowTests(unittest.TestCase):
    def test_model_top_level_command_is_normalized(self):
        decision = agent.parse_decisions(
            '{"action":"run_command","command":"npm install @vue/runtime-core @vue/template-core"}'
        )[0]
        self.assertEqual(
            agent.normalize_tool_args(decision),
            {"command": "npm install @vue/runtime-core @vue/template-core"},
        )

    def test_list_files_on_file_is_classified_and_read_file_verifies_recovery(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target = root / "package.json"
            target.write_text('{"name":"probe"}', encoding="utf-8")
            failed = agent.run_tool(root, "list_files", {"path": "package.json"})
            issue = agent.classify_tool_failure("list_files", {"path": "package.json"}, failed)
            self.assertEqual(issue, "list_files_used_on_file")
            content = agent.run_tool(root, "read_file", {"path": "package.json"})
            self.assertTrue(agent.verification_succeeded(issue, "list_files", {"path": "package.json"}, "read_file", {"path": "package.json"}, content))

    def test_missing_dev_script_is_stopped_before_process_launch(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "package.json").write_text('{"scripts":{"test":"echo ok"}}', encoding="utf-8")
            with patch.object(agent.subprocess, "Popen") as popen:
                output = agent.run_tool(root, "run_command", {"command": "npm run dev"})
            popen.assert_not_called()
            self.assertIn("没有 scripts.dev", output)
            self.assertEqual(agent.classify_tool_failure("run_command", {"command": "npm run dev"}, output), "npm_run_dev_script_missing")

    def test_background_npm_failure_is_not_reported_as_started(self):
        class FailedProcess:
            pid = 321
            returncode = None
            stdout = io.StringIO('npm error Missing script: "dev"\n')

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                self.returncode = 1
                return self.returncode

        with patch.object(agent.subprocess, "Popen", return_value=FailedProcess()):
            output = agent.start_background_command(Path(tempfile.gettempdir()), "npm run dev")
        self.assertIn("后台命令启动失败", output)
        self.assertNotIn("后台进程已启动", output)

    def test_identical_failed_action_is_blocked_until_repair(self):
        issue = {"action": "list_files", "args": {"path": "package.json"}}
        self.assertTrue(agent.should_block_repeat(issue, [], "list_files", {"path": "package.json"}))
        self.assertFalse(agent.should_block_repeat(issue, ["read package.json"], "list_files", {"path": "package.json"}))

    def test_only_verified_experience_is_persisted(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "experiences.json"
            agent.record_verified_experience({"issue": "unverified", "lesson": "no proof"}, path)
            self.assertEqual(agent.load_experiences(path), [])
            agent.record_verified_experience({"issue": "verified", "lesson": "use read_file", "proof": "read succeeded"}, path)
            entries = agent.load_experiences(path)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["issue"], "verified")

    def test_create_directory_action_creates_directory_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            created = agent.run_tool(root, "create_directory", {"path": "src/components"})
            repeated = agent.run_tool(root, "create_directory", {"path": "src/components"})
            self.assertTrue((root / "src" / "components").is_dir())
            self.assertTrue(created.startswith("已创建目录："))
            self.assertTrue(repeated.startswith("目录已存在："))

    def test_write_file_creates_missing_parent_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            output = agent.run_tool(root, "write_file", {"path": "src/index.html", "content": "<h1>ok</h1>"})
            self.assertTrue(output.startswith("已写入："))
            self.assertEqual((root / "src" / "index.html").read_text(encoding="utf-8"), "<h1>ok</h1>")

    def test_write_file_rejects_directory_like_target_and_mkdir_text(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            directory_target = agent.run_tool(root, "write_file", {"path": "src", "content": "<h1>site</h1>"})
            command_content = agent.run_tool(root, "write_file", {"path": "workspace", "content": "mkdir src"})
            self.assertIn("看起来是目录路径", directory_target)
            self.assertIn("不会执行 mkdir", command_content)
            self.assertFalse((root / "src").exists())
            self.assertFalse((root / "workspace").exists())

    def test_file_directory_collision_can_be_resolved_without_losing_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "src").write_text("preserve this", encoding="utf-8")
            directory_conflict = agent.run_tool(root, "create_directory", {"path": "src"})
            self.assertIn("已是文件", directory_conflict)
            self.assertEqual(agent.classify_tool_failure("create_directory", {"path": "src"}, directory_conflict), "path_component_is_file")
            conflict = agent.run_tool(root, "write_file", {"path": "src/index.html", "content": "<h1>site</h1>"})
            self.assertIn("父路径 `src` 已是文件", conflict)
            self.assertEqual(agent.classify_tool_failure("write_file", {"path": "src/index.html"}, conflict), "path_component_is_file")
            moved = agent.run_tool(root, "move_file", {"source": "src", "destination": "src-original.txt"})
            self.assertTrue(moved.startswith("已移动文件："))
            self.assertEqual((root / "src-original.txt").read_text(encoding="utf-8"), "preserve this")
            self.assertTrue(agent.run_tool(root, "create_directory", {"path": "src"}).startswith("已创建目录："))
            written = agent.run_tool(root, "write_file", {"path": "src/index.html", "content": "<h1>site</h1>"})
            self.assertTrue(written.startswith("已写入："))
            self.assertTrue((root / "src" / "index.html").is_file())

    def test_move_file_never_overwrites_existing_destination(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "source.txt").write_text("source", encoding="utf-8")
            (root / "target.txt").write_text("keep target", encoding="utf-8")
            output = agent.run_tool(root, "move_file", {"source": "source.txt", "destination": "target.txt"})
            self.assertIn("目标已存在，未覆盖", output)
            self.assertEqual((root / "source.txt").read_text(encoding="utf-8"), "source")
            self.assertEqual((root / "target.txt").read_text(encoding="utf-8"), "keep target")


if __name__ == "__main__":
    unittest.main()
