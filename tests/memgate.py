"""Regression tests using synthetic processes and isolated configuration/state."""

import contextlib
import copy
import dataclasses
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


ROOT = Path(sys.argv[1]).resolve()
TEMP = Path(sys.argv[2]).resolve()
SCRIPT = ROOT / "plugins/memgate/skills/memgate/scripts/memgate"
loader = importlib.machinery.SourceFileLoader("memgate_test_module", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
memgate = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = memgate
loader.exec_module(memgate)

if len(sys.argv) > 3 and sys.argv[3] == "--fixture-run":
    # Each child reads the same test-only state and memory snapshot.
    memgate.meminfo = lambda: (5000, 8000, 0, 0)
    sys.exit(memgate.run(sys.argv[4:], memgate.load_config()))


def snapshot(lv="WARN", suspects=None):
    return dict(level=lv, avail=3000, total=8000, swap=0, psi=0,
                usage={}, wss={}, leads={}, suspects=[] if suspects is None else suspects)


def process(pid=10, **changes):
    result = dict(pid=pid, comm="node", ppid=1, rss=200, age=2400,
                  args="node server", ws="a", pane=None, cwd="/srv/projects/demo", cg="")
    result.update(changes)
    return result


class MemgateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=TEMP)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        env = patch.dict(os.environ, {"HOME": str(self.base / "home"),
            "XDG_CONFIG_HOME": str(self.base / "config"), "XDG_STATE_HOME": str(self.base / "state")})
        env.start()
        self.addCleanup(env.stop)
        self.cfg = memgate.Config(repo_roots=["/srv/projects"])

    def write_config(self, text):
        path = memgate.config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def test_config_xdg_fallback_and_validation(self):
        self.assertEqual(memgate.config_path(), self.base / "config/memgate/config.toml")
        self.assertEqual(memgate.state_path(), self.base / "state/memgate")
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": "", "XDG_STATE_HOME": ""}):
            self.assertEqual(memgate.config_path(), self.base / "home/.config/memgate/config.toml")
            self.assertEqual(memgate.state_path(), self.base / "home/.local/state/memgate")
        self.assertIsNone(memgate.load_config().oom_score_adj)
        self.write_config("warn_mb = 5000\ncritical_mb = 1000\npsi_threshold = 4.5\n")
        cfg = memgate.load_config()
        self.assertEqual((cfg.warn_mb, cfg.critical_mb, cfg.psi_threshold), (5000, 1000, 4.5))
        for value in ("unknown = 1", "warn_mb = true", "critical_mb = 0", "warn_mb = 1",
                      "oom_score_adj = -1001", "protected_ports = [0]", "repo_roots = ['relative']",
                      "ignore_patterns = ['[']", "psi_threshold = nan"):
            with self.subTest(value=value):
                self.write_config(value)
                with self.assertRaises((ValueError, memgate.re.error)):
                    memgate.load_config()

    def test_example_config_parses(self):
        self.write_config((SCRIPT.parent.parent / "config.example.toml").read_text())
        cfg = memgate.load_config()
        self.assertEqual(cfg.protected_ports, [8456])
        self.assertIsNone(cfg.oom_score_adj)

    def test_gate_boundary_and_configured_psi(self):
        cfg = dataclasses.replace(self.cfg, warn_mb=5000, critical_mb=1000, psi_threshold=4.5)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            for available, psi, expected in ((2999, 0, 1), (3000, 0, 0), (3001, 4.5, 1), (3001, 4.49, 0)):
                with patch.object(memgate, "meminfo", return_value=(available, 8000, 0, psi)):
                    self.assertEqual(memgate.gate(2000, cfg), expected)
        self.assertIn("critical_floor=1000MB psi_limit=4.5", output.getvalue())
        self.assertEqual(memgate.level(5000, 0, cfg), "OK")
        self.assertEqual(memgate.level(4999, 0, cfg), "WARN")
        self.assertEqual(memgate.level(1000, 0, cfg), "WARN")
        self.assertEqual(memgate.level(999, 0, cfg), "CRIT")
        self.assertEqual(memgate.level(5000, 4.5, cfg), "WARN")
        self.assertIn("WARN below 5000MB", memgate.report(snapshot(), cfg))

    def collect(self, processes, available=3000, wsl=False, ports=None, workspaces=None,
                panes=None, agents=None, docker="", stats="", inspect="", cfg=None):
        workspaces = [{"workspace_id": "a", "label": "A"}, {"workspace_id": "b", "label": "B"}] if workspaces is None else workspaces
        panes = [{"workspace_id": "a", "cwd": "/srv/projects/demo"}] if panes is None else panes
        agents = [{"workspace_id": "a", "name": "lead-demo", "agent_status": "idle"}] if agents is None else agents
        def fake_herdr(*args):
            return {("workspace", "list"): {"workspaces": workspaces},
                    ("pane", "list"): {"panes": panes},
                    ("agent", "list"): {"agents": agents}}[args]
        def fake_sh(command, **kwargs):
            self.assertEqual(command[0], "docker")
            return {"ps": docker, "stats": stats, "inspect": inspect}[command[1]]
        with patch.object(memgate, "meminfo", return_value=(available, 8000, 0, 0)), \
             patch.object(memgate, "procs", return_value=copy.deepcopy(processes)), \
             patch.object(memgate, "listening", return_value=ports or {}), \
             patch.object(memgate, "is_wsl", return_value=wsl), \
             patch.object(memgate, "herdr", side_effect=fake_herdr), \
             patch.object(memgate, "sh", side_effect=fake_sh):
            return memgate.analyze(cfg or self.cfg, with_docker_stats=True)

    def test_cwd_wins_over_inherited_workspace(self):
        result = self.collect({10: process(ws="b")})
        self.assertEqual(result["usage"], {"a": 200})
        result = self.collect({10: process(ws="missing", cwd="/srv/projects/demo/build")})
        self.assertEqual(result["usage"], {"a": 200})
        self.assertEqual(result["suspects"], [])
        result = self.collect({10: process(ws="b")}, panes=[
            {"workspace_id": "a", "cwd": "/srv/projects/demo"},
            {"workspace_id": "b", "cwd": "/srv/projects/demo"}])
        self.assertEqual(result["usage"], {"b": 200})
        result = self.collect({10: process(ws="a", cwd="/srv/projects/demo/child")}, panes=[
            {"workspace_id": "a", "cwd": "/srv/projects/demo"},
            {"workspace_id": "b", "cwd": "/srv/projects/demo/child"}])
        self.assertEqual(result["usage"], {"b": 200})

    def test_git_repository_fallback(self):
        repo = self.base / "demo"
        (repo / "src").mkdir(parents=True)
        (repo / ".git").write_text("gitdir: /some/git/worktree")
        self.assertEqual(memgate.repo_of(str(repo / "src"), memgate.Config()), "demo")

    def test_report_lists_all_workspace_leads(self):
        agents = [
            {"workspace_id": "a", "name": "lead-b", "agent_status": "idle"},
            {"workspace_id": "a", "name": "impl-demo", "agent_status": "idle"},
            {"workspace_id": "a", "name": "lead-a", "agent_status": "idle"},
            {"workspace_id": "b", "name": "lead-other", "agent_status": "idle"},
        ]
        result = self.collect({10: process()}, ports={10: {8001}}, agents=agents)
        self.assertEqual(result["leads"], {"a": "lead-a,lead-b", "b": "lead-other"})
        self.assertIn("A [a] lead=lead-a,lead-b", memgate.report(result, self.cfg))
        self.assertIn("lead-a,lead-b", result["suspects"][0][3])

    def test_wsl_orphan_only_on_wsl_and_not_services(self):
        processes = {1: process(pid=1, comm="init", rss=0, ppid=0, cwd="", ws=None), 10: process()}
        self.assertEqual(self.collect(processes, wsl=False)["suspects"], [])
        self.assertEqual(self.collect(processes, wsl=True)["suspects"][0][0], "orphan:10")
        processes[10]["cg"] = "0::/system.slice/example.service\n"
        self.assertEqual(self.collect(processes, wsl=True)["suspects"], [])

    def test_mcp_and_small_idle_servers_are_excluded(self):
        processes = {9: process(pid=9, comm="tool", args="example-mcp server", rss=0),
                     10: process(ppid=9)}
        self.assertEqual(self.collect(processes, ports={10: {8001}})["suspects"], [])
        self.assertEqual(self.collect({10: process(rss=149)}, ports={10: {8001}})["suspects"], [])
        result = self.collect({10: process(rss=150)}, ports={10: {8001}})
        self.assertEqual(result["suspects"][0][0], "idle:10")
        self.assertEqual(self.collect({10: process()}, available=5000, ports={10: {8001}})["suspects"], [])

    def test_duplicate_servers_ignore_parent_child_and_ok_level(self):
        processes = {pid: process(pid=pid) for pid in (10, 11, 12)}
        ports = {pid: {8001 + pid} for pid in processes}
        result = self.collect(processes, ports=ports)
        self.assertIn("dup:a:3", [s[0] for s in result["suspects"]])
        processes[12]["ppid"] = 11
        result = self.collect(processes, ports=ports)
        self.assertNotIn("dup:a:3", [s[0] for s in result["suspects"]])
        self.assertEqual(self.collect(processes, ports=ports, available=5000)["suspects"], [])

    def test_protections_and_no_oom_mutation_by_default(self):
        cfg = dataclasses.replace(self.cfg, protected_ports=[8001],
            protected_repositories=["demo"], protected_services=["demo.service"])
        with patch.object(memgate.Path, "write_text", side_effect=AssertionError("No OOM writes")):
            self.assertEqual(self.collect({10: process()}, ports={10: {8001}}, cfg=cfg)["suspects"], [])
        cfg = dataclasses.replace(self.cfg, protected_services=["demo.service"])
        result = self.collect({10: process(ws="missing", cwd="/other", cg="0::/user/demo.service\n")}, cfg=cfg)
        self.assertEqual(result["suspects"], [])
        cfg = dataclasses.replace(cfg, oom_score_adj=-500, oom_use_sudo=True)
        with patch.object(memgate.Path, "read_text", return_value="0"), patch.object(memgate, "sh") as command:
            memgate.adjust_oom(process(), cfg)
        command.assert_called_once_with(["sudo", "-n", "choom", "-n", "-500", "-p", "10"])

    def test_docker_attribution_exclusion_stats_and_ignore(self):
        cfg = dataclasses.replace(self.cfg, docker_exclude=["keep-project", "keep-name"],
                                  ignore_patterns=[r"^docker:retained$"])
        docker = ("demo-db\tdemo\tan hour\nother-db\tother\tan hour\n"
                  "keep-db\tkeep-project\tan hour\nkeep-name\tkeep\tan hour\n"
                  "retained-db\tretained\tan hour\nsupabase_db_example\t\tan hour\n")
        stats = "other-db\t1.5GiB / 4GiB\nsupabase_db_example\t256MiB / 4GiB\ninvalid line\n"
        result = self.collect({}, cfg=cfg, docker=docker, stats=stats)
        self.assertEqual([s[0] for s in result["suspects"]], ["docker:other", "docker:example"])
        self.assertIn("1536MB", result["suspects"][0][2])
        self.assertEqual(memgate.docker_memory("512KiB / 1GiB"), 0.5)

    def started(self, seconds_ago):
        stamp = time.gmtime(time.time() - seconds_ago)
        return time.strftime("%Y-%m-%dT%H:%M:%S", stamp) + ".123456789Z"

    def test_docker_skips_young_and_short_lived_autoremove_containers(self):
        docker = ("young\t\t15 seconds ago\nrm-short\t\t15 minutes ago\n"
                  "rm-long\t\t2 hours ago\nold\t\t2 hours ago\nunknown\t\tan hour\n")
        inspect = (f"/young\t{self.started(15)}\ttrue\n"
                   f"/rm-short\t{self.started(900)}\ttrue\n"
                   f"/rm-long\t{self.started(7200)}\ttrue\n"
                   f"/old\t{self.started(7200)}\tfalse\n")
        result = self.collect({}, docker=docker, inspect=inspect)
        self.assertEqual(result["level"], "WARN")
        self.assertEqual(sorted(s[0] for s in result["suspects"]), ["docker:old", "docker:rm-long", "docker:unknown"])
        self.assertEqual(memgate.docker_details([]), {})

    def test_docker_at_ok_reports_only_long_running_large_projects(self):
        docker = "small\t\t2 hours\nlarge\t\t2 hours\nrecent\t\t20 minutes\n"
        inspect = (f"/small\t{self.started(7200)}\tfalse\n"
                   f"/large\t{self.started(7200)}\tfalse\n"
                   f"/recent\t{self.started(1200)}\tfalse\n")
        stats = "small\t20MiB / 1GiB\nlarge\t900MiB / 4GiB\nrecent\t900MiB / 4GiB\n"
        result = self.collect({}, available=6000, docker=docker, inspect=inspect, stats=stats)
        self.assertEqual(result["level"], "OK")
        self.assertEqual([s[0] for s in result["suspects"]], ["docker:large"])
        # Below the warning floor every old project is reported again.
        result = self.collect({}, available=3000, docker=docker, inspect=inspect, stats=stats)
        self.assertEqual(sorted(s[0] for s in result["suspects"]), ["docker:large", "docker:recent", "docker:small"])

    def test_missing_herdr_does_not_claim_workspaces_closed(self):
        with patch.object(memgate, "meminfo", return_value=(5000, 8000, 0, 0)), \
             patch.object(memgate, "procs", return_value={10: process(ws="unknown")}), \
             patch.object(memgate, "listening", return_value={}), \
             patch.object(memgate, "herdr", return_value={}), \
             patch.object(memgate, "sh", return_value=""), \
             patch.object(memgate, "is_wsl", return_value=False):
            self.assertEqual(memgate.analyze(self.cfg)["suspects"], [])

    def test_rss_uses_runtime_page_size(self):
        proc = self.base / "proc"
        child = proc / "10"
        child.mkdir(parents=True)
        (proc / "uptime").write_text("1000 0")
        fields = ["S", "1"] + ["0"] * 17 + ["100"]
        (child / "stat").write_text("10 (node worker) " + " ".join(fields))
        (child / "statm").write_text("500 256")
        (child / "cmdline").write_bytes(b"node\0worker\0")
        (child / "environ").write_bytes(b"HERDR_WORKSPACE_ID=a\0")
        (child / "cgroup").write_text("")
        def fake_path(value):
            path = Path(value)
            return proc / path.relative_to("/proc") if path.is_relative_to("/proc") else path
        with patch.object(memgate, "Path", side_effect=fake_path), \
             patch.object(memgate.os, "sysconf", side_effect=lambda key: 8192 if key == "SC_PAGE_SIZE" else 100):
            result = memgate.procs()
        self.assertEqual(result[10]["rss"], 2)
        self.assertEqual(result[10]["comm"], "node worker")

    def drive_loop(self, results, agent_statuses, cycles, prompt_result=None):
        calls, sleeps = [], 0
        statuses = iter(agent_statuses)
        def fake_herdr(*args):
            if args[:2] == ("agent", "list"):
                return {"agents": [{"name": "coordinator", "agent_status": next(statuses)}]}
            self.assertEqual(args[:3], ("agent", "prompt", "coordinator"))
            calls.append(args)
            return {"success": True} if prompt_result is None else prompt_result
        def fake_sleep(_):
            nonlocal sleeps
            sleeps += 1
            if sleeps >= cycles:
                raise KeyboardInterrupt
        with patch.object(memgate, "analyze", side_effect=results) as analyze, \
             patch.object(memgate, "herdr", side_effect=fake_herdr), \
             patch.object(memgate.time, "sleep", side_effect=fake_sleep):
            with self.assertRaises(KeyboardInterrupt):
                memgate.loop("coordinator", self.cfg)
        self.assertFalse((memgate.state_path() / "loop.pid").exists())
        self.assertFalse((memgate.state_path() / "loop.err").exists())
        return calls, analyze.call_args_list

    def test_loop_discards_resolved_pending_warning(self):
        suspect = [("idle:10", "idle", "description", "review")]
        calls, _ = self.drive_loop([snapshot(suspects=suspect), snapshot("OK")], ["working"], 2)
        self.assertEqual(calls, [])
        self.assertEqual(list(memgate.state_path().glob("event-*.md")), [])

    def test_loop_rechecks_after_docker_collection(self):
        calls, args = self.drive_loop([snapshot(), snapshot("OK")], ["idle"], 1)
        self.assertEqual(calls, [])
        self.assertTrue(args[1].kwargs["with_docker_stats"])

    def test_loop_key_comes_from_final_snapshot(self):
        first = snapshot(suspects=[("idle:10", "idle", "old", "review")])
        final = snapshot(suspects=[("docker:example", "docker", "new", "review")])
        calls, _ = self.drive_loop([first, final, final], ["idle", "idle"], 2)
        self.assertEqual(len(calls), 1)
        event = Path(calls[0][-1].split(": ", 1)[1])
        self.assertIn("docker:example", event.read_text())
        self.assertNotIn("idle:10", event.read_text())

    def test_loop_busy_then_idle_delivery_and_retry(self):
        warning = snapshot()
        calls, _ = self.drive_loop([warning, warning, warning], ["working", "idle", "idle"], 2)
        self.assertEqual(len(calls), 1)

    def test_failed_prompt_is_retried(self):
        warning = snapshot()
        calls, _ = self.drive_loop([warning, warning, warning, warning], ["idle"] * 4, 2, prompt_result={})
        self.assertEqual(len(calls), 2)

    def drive_loop_with_docker_change(self, change, cycles=1, after_cycle=None):
        # Keep analyze real: Docker collection mutates its underlying dependencies.
        state = dict(avail=3000, processes={10: process()}, ports={10: {8001}},
                     workspaces=[{"workspace_id": "a", "label": "A"},
                                 {"workspace_id": "b", "label": "B"}],
                     panes=[{"workspace_id": "a", "cwd": "/srv/projects/demo"}],
                     agents=[{"workspace_id": "a", "name": "lead-demo", "agent_status": "idle"},
                             {"workspace_id": "b", "name": "coordinator", "agent_status": "idle"}])
        prompts, samples, agent_reads = [], [], []
        rounds, stats_reads = 0, 0
        def fake_memory():
            samples.append(state["avail"])
            return state["avail"], 8000, 0, 0
        def fake_herdr(*args):
            if args == ("workspace", "list"):
                return {"workspaces": copy.deepcopy(state["workspaces"])}
            if args == ("pane", "list"):
                return {"panes": copy.deepcopy(state["panes"])}
            if args == ("agent", "list"):
                agent_reads.append(state["agents"][1]["agent_status"])
                return {"agents": copy.deepcopy(state["agents"])}
            self.assertEqual(args[:3], ("agent", "prompt", "coordinator"))
            self.assertIn(state["agents"][1]["agent_status"], ("idle", "done"))
            prompts.append(args)
            return {"success": True}
        def fake_docker(command, **kwargs):
            nonlocal stats_reads
            self.assertEqual(command[0], "docker")
            if command[1] == "stats":
                stats_reads += 1
                if stats_reads == 1:
                    change(state)
                return "demo-db\t64MiB / 1GiB\n"
            if command[1] == "inspect":
                return ""
            self.assertEqual(command[1], "ps")
            return "demo-db\tdemo\tan hour\n"
        def fake_sleep(_):
            nonlocal rounds
            rounds += 1
            if rounds >= cycles:
                raise KeyboardInterrupt
            if after_cycle:
                after_cycle(state)
        with patch.object(memgate, "meminfo", side_effect=fake_memory), \
             patch.object(memgate, "procs", side_effect=lambda: copy.deepcopy(state["processes"])), \
             patch.object(memgate, "listening", side_effect=lambda: copy.deepcopy(state["ports"])), \
             patch.object(memgate, "is_wsl", return_value=False), \
             patch.object(memgate, "herdr", side_effect=fake_herdr), \
             patch.object(memgate, "sh", side_effect=fake_docker), \
             patch.object(memgate.time, "sleep", side_effect=fake_sleep):
            with self.assertRaises(KeyboardInterrupt):
                memgate.loop("coordinator", self.cfg)
        self.assertFalse((memgate.state_path() / "loop.pid").exists())
        self.assertFalse((memgate.state_path() / "loop.err").exists())
        return prompts, samples, agent_reads

    def test_loop_samples_recovered_memory_after_docker_stats(self):
        calls, samples, _ = self.drive_loop_with_docker_change(lambda state: state.update(avail=5000))
        self.assertEqual(samples, [3000, 5000])
        self.assertEqual(calls, [])
        self.assertEqual(list(memgate.state_path().glob("event-*.md")), [])
        report = (memgate.state_path() / "status.md").read_text()
        self.assertIn("Level: **OK** available=5000MB", report)
        self.assertNotIn("idle:10", report)

    def test_loop_rechecks_agent_after_docker_stats(self):
        for status in ("working", "blocked"):
            with self.subTest(status=status):
                def change(state):
                    state["agents"][1]["agent_status"] = status
                calls, _, reads = self.drive_loop_with_docker_change(change)
                self.assertEqual(calls, [])
                self.assertEqual(reads, ["idle", "idle", status, status])
                self.assertEqual(list(memgate.state_path().glob("event-*.md")), [])

    def test_loop_busy_target_is_retried_when_idle_again(self):
        def change(state):
            state["agents"][1]["agent_status"] = "blocked"
        def recover(state):
            state["agents"][1]["agent_status"] = "idle"
        calls, _, _ = self.drive_loop_with_docker_change(change, cycles=2, after_cycle=recover)
        self.assertEqual(len(calls), 1)

    def test_loop_rebuilds_process_candidates_after_docker_stats(self):
        def change(state):
            state["processes"] = {}
            state["ports"] = {}
        calls, _, _ = self.drive_loop_with_docker_change(change)
        self.assertEqual(len(calls), 1)
        self.assertIn("suspects=0", calls[0][-1])
        event = Path(calls[0][-1].split(": ", 1)[1]).read_text()
        self.assertIn("Level: **WARN**", event)
        self.assertNotIn("idle:10", event)

    def test_loop_rebuilds_workspace_candidates_after_docker_stats(self):
        def change(state):
            state["panes"] = []
            state["agents"] = [state["agents"][0] | {"workspace_id": "b"}, state["agents"][1]]
            state["workspaces"] = [{"workspace_id": "b", "label": "B"}]
        calls, _, _ = self.drive_loop_with_docker_change(change)
        self.assertEqual(len(calls), 1)
        event = Path(calls[0][-1].split(": ", 1)[1]).read_text()
        self.assertIn("closedws:10", event)
        self.assertIn("docker:demo", event)
        self.assertNotIn("idle:10", event)

    def test_loop_lock_prevents_duplicate_and_ignores_stale_pid(self):
        state = memgate.state_path()
        state.mkdir(parents=True)
        (state / "loop.pid").write_text("1")
        with (state / "loop.lock").open("a+") as lock:
            memgate.fcntl.flock(lock, memgate.fcntl.LOCK_EX)
            with contextlib.redirect_stdout(io.StringIO()), patch.object(memgate, "analyze") as analyze:
                self.assertEqual(memgate.loop("coordinator", self.cfg), 0)
                analyze.assert_not_called()
        self.drive_loop([snapshot("OK")], [], 1)

    def test_notifications_reminders_and_recovery(self):
        warning, ok = snapshot(), snapshot("OK")
        key = memgate.notification_key(warning)
        self.assertFalse(memgate.should_notify(warning, key, "WARN", 0, 599, self.cfg))
        self.assertTrue(memgate.should_notify(warning, key, "WARN", 0, 600, self.cfg))
        self.assertTrue(memgate.should_notify(ok, key, "WARN", 0, 10, self.cfg))
        self.assertFalse(memgate.should_notify(ok, None, "OK", None, 10, self.cfg))

    def fixture_command(self, slot, maximum, wait, command):
        return [sys.executable, str(Path(__file__).resolve()), str(ROOT), str(TEMP), "--fixture-run",
                "--slot", slot, "--max", str(maximum), "--need", "0", "--wait", str(wait), "--", *command]

    def wait_file(self, path, child):
        deadline = time.monotonic() + 5
        while not path.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(path.exists(), "Synthetic slot holder did not start")

    def test_run_concurrent_slots_timeout_and_release(self):
        children, releases = [], []
        self.write_config("run_poll_seconds = 0.01\n")
        try:
            for index in range(2):
                ready, release = self.base / f"ready-{index}", self.base / f"release-{index}"
                waiting = f"while not Path({str(release)!r}).exists():\n time.sleep(0.01)"
                code = ("from pathlib import Path; import time; "
                        f"Path({str(ready)!r}).touch(); exec({waiting!r})")
                child = subprocess.Popen(self.fixture_command("tests", 2, 1, [sys.executable, "-c", code]),
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                children.append(child)
                releases.append(release)
                self.wait_file(ready, child)
            status = memgate.slots_status()
            self.assertEqual(sum(": busy " in line for line in status), 2)
            command = self.fixture_command("tests", 2, 0.1, [sys.executable, "-c", "raise SystemExit(0)"])
            started = time.monotonic()
            completed = subprocess.run(command, capture_output=True, text=True, timeout=5)
            self.assertEqual(completed.returncode, 75)
            self.assertGreaterEqual(time.monotonic() - started, 0.1)
            conflict = subprocess.run(self.fixture_command("tests", 3, 0, ["unused"]), capture_output=True, text=True, timeout=5)
            self.assertEqual(conflict.returncode, 2)
            releases[0].touch()
            children[0].communicate(timeout=5)
            completed = subprocess.run(self.fixture_command("tests", 2, 0, [sys.executable, "-c", "raise SystemExit(7)"]),
                                       capture_output=True, text=True, timeout=5)
            self.assertEqual(completed.returncode, 7)
        finally:
            for release in releases:
                release.touch()
            for child in children:
                child.communicate(timeout=5)
        self.assertEqual(sum(": free" in line for line in memgate.slots_status()), 2)

    def test_run_memory_timeout_does_not_execute_and_releases_lock(self):
        with patch.object(memgate, "meminfo", return_value=(1000, 8000, 0, 0)), \
             patch.object(memgate.os, "execvp", side_effect=AssertionError("Must not start")), \
             contextlib.redirect_stderr(io.StringIO()):
            code = memgate.run(["--slot", "low", "--max", "1", "--need", "0", "--wait", "0", "--", "unused"], self.cfg)
        self.assertEqual(code, 75)
        self.assertEqual(memgate.slots_status(), ["low/0: free"])

    def test_run_waits_for_memory_then_executes(self):
        with patch.object(memgate, "meminfo", side_effect=[(1000, 8000, 0, 0), (5000, 8000, 0, 0)]), \
             patch.object(memgate.time, "sleep"), \
             patch.object(memgate.os, "execvp", side_effect=FileNotFoundError) as execute, \
             contextlib.redirect_stderr(io.StringIO()):
            code = memgate.run(["--slot", "wait", "--max", "1", "--need", "0", "--wait", "1", "--", "missing"], self.cfg)
        execute.assert_called_once_with("missing", ["missing"])
        self.assertEqual(code, 127)
        self.assertEqual(memgate.slots_status(), ["wait/0: free"])

    def test_invalid_run_and_gate_inputs(self):
        for values in (["--slot", "..", "--max", "1"], ["--slot", "a", "--max", "0"],
                       ["--slot", "a", "--max", "1", "--need", "-1"],
                       ["--slot", "a", "--max", "1", "--wait", "nan"]):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                memgate.run([*values, "--", "unused"], self.cfg)
            self.assertEqual(error.exception.code, 2)
        with patch.object(sys, "argv", [str(SCRIPT), "gate", "-1"]), \
             contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            memgate.main()


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]], verbosity=2)
