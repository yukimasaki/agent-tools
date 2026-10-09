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
import shutil
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
                      "ignore_patterns = ['[']", "psi_threshold = nan",
                      "brief_leads = 'yes'", "roster_batch_seconds = -1"):
            with self.subTest(value=value):
                self.write_config(value)
                with self.assertRaises((ValueError, memgate.re.error)):
                    memgate.load_config()

    def test_example_config_parses(self):
        self.write_config((SCRIPT.parent.parent / "config.example.toml").read_text())
        cfg = memgate.load_config()
        self.assertEqual(cfg.protected_ports, [8456])
        self.assertIsNone(cfg.oom_score_adj)
        self.assertIs(cfg.brief_leads, False)
        self.assertIs(cfg.notify_new_workers, False)

    def test_short_psi_spike_counts_only_near_the_warning_floor(self):
        cfg = dataclasses.replace(self.cfg, warn_mb=5000, critical_mb=1000, psi_threshold=8)
        # avg10 spike, avg60 calm, plenty available: slow but safe.
        self.assertEqual(memgate.level(19000, (15.7, 2.0), cfg), "OK")
        # The same spike counts once available is under 1.5x the warning floor.
        self.assertEqual(memgate.level(7000, (15.7, 2.0), cfg), "WARN")
        # Sustained pressure always counts.
        self.assertEqual(memgate.level(19000, (2.0, 9.0), cfg), "WARN")
        with contextlib.redirect_stdout(io.StringIO()) as output:
            with patch.object(memgate, "meminfo", return_value=(19000, 40000, 0, (15.7, 2.0))):
                self.assertEqual(memgate.gate(3000, cfg), 0)
            # Projected availability below 1.5x the floor makes the spike count.
            with patch.object(memgate, "meminfo", return_value=(9000, 40000, 0, (15.7, 2.0))):
                self.assertEqual(memgate.gate(3000, cfg), 1)
            with patch.object(memgate, "meminfo", return_value=(19000, 40000, 0, (2.0, 9.0))):
                self.assertEqual(memgate.gate(3000, cfg), 1)
        self.assertIn("PSI some10=15.7 some60=2.0", memgate.report(dict(snapshot(), psi=(15.7, 2.0)), cfg))

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

    def test_main_repo_of_resolves_worktrees(self):
        with tempfile.TemporaryDirectory() as tmp:
            main, worktree = Path(tmp) / "mainrepo", Path(tmp) / "mainrepo-12"
            env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
                       GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
            for args in (["init", "-q", str(main)],
                         ["-C", str(main), "commit", "-q", "--allow-empty", "-m", "init"],
                         ["-C", str(main), "worktree", "add", "-q", "-b", "wt", str(worktree)]):
                subprocess.run(["git", *args], check=True, env=env)
            memgate.main_repo_of.cache_clear()
            self.assertEqual(memgate.main_repo_of(str(worktree)), "mainrepo")
            self.assertEqual(memgate.main_repo_of(str(main)), "mainrepo")
            self.assertIsNone(memgate.main_repo_of(tmp))

    def test_docker_of_main_repo_is_not_orphaned_by_worktree_panes(self):
        panes = [{"workspace_id": "a", "cwd": "/srv/projects/mainrepo-12"}]
        docker = "supabase_db_mainrepo\t\tan hour\nother-db\tother\tan hour\n"
        with patch.object(memgate, "main_repo_of", side_effect=lambda cwd: "mainrepo" if cwd.endswith("-12") else None), \
             patch.object(memgate.Path, "is_dir", return_value=True):
            result = self.collect({}, panes=panes, docker=docker)
        self.assertEqual([s[0] for s in result["suspects"]], ["docker:other"])

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

    def drive_roster(self, rounds, cfg, prompt_result=None, analyze=None, step=30.0, reset=True, check_err=True):
        """rounds: list of dict(agents=[...] or None, workspaces=[...] or None,
        screens={target: text}, coordinator="idle" or None for absent). Returns [(round, target, text)]."""
        if reset:
            shutil.rmtree(memgate.state_path(), ignore_errors=True)
        prompts, index = [], 0
        def coordinator(r):
            status = r.get("coordinator", "idle")
            return [] if status is None else [{"workspace_id": "wz", "pane_id": "wz:p1", "name": "coordinator",
                                               "agent": "pi", "agent_status": status}]
        def current():
            return rounds[min(index, len(rounds) - 1)]
        def fake_herdr(*args):
            r = current()
            if args == ("agent", "list"):
                return {} if r.get("agents") is None else {"agents": copy.deepcopy(r["agents"]) + coordinator(r)}
            if args == ("workspace", "list"):
                return {} if r.get("workspaces") is None else {
                    "workspaces": copy.deepcopy(r["workspaces"]) + [{"workspace_id": "wz", "label": "Z"}]}
            self.assertEqual(args[:2], ("agent", "prompt"))
            prompts.append((index, args[2], args[3]))
            return {"success": True} if prompt_result is None else prompt_result(index, args[2])
        def fake_text(*args):
            self.assertEqual(args[:2], ("agent", "read"))
            return current().get("screens", {}).get(args[2], "❯ \n")
        def fake_sleep(_):
            nonlocal index
            index += 1
            if index >= len(rounds):
                raise KeyboardInterrupt
        with patch.object(memgate, "analyze", side_effect=analyze or (lambda *a, **k: snapshot("OK"))), \
             patch.object(memgate, "herdr", side_effect=fake_herdr), \
             patch.object(memgate, "herdr_text", side_effect=fake_text), \
             patch.object(memgate.time, "time", side_effect=lambda: 1_000_000.0 + step * index), \
             patch.object(memgate.time, "monotonic", side_effect=lambda: 1000.0 + step * index), \
             patch.object(memgate.time, "sleep", side_effect=fake_sleep):
            with self.assertRaises(KeyboardInterrupt):
                memgate.loop("coordinator", cfg)
        if check_err:
            self.assertFalse((memgate.state_path() / "loop.err").exists())
        return prompts

    def roster_cfg(self, **changes):
        rules = self.base / "rules.md"
        rules.write_text("Shared rules\n")
        values = dict(brief_leads=True, notify_new_workers=True, roster_batch_seconds=0,
                      brief_message="Read the shared memory rules: {rules_file}", rules_file=str(rules))
        return dataclasses.replace(self.cfg, **{**values, **changes})

    def agent(self, ws, pane, name=None, status="idle", kind="claude", cwd="/srv/projects/demo", **extra):
        result = dict(workspace_id=ws, pane_id=f"{ws}:{pane}", agent=kind, cwd=cwd, agent_status=status, **extra)
        if name:
            result["name"] = name
        return result

    def ledger(self):
        return json.loads((memgate.state_path() / "roster.json").read_text())

    @staticmethod
    def spaces(*pairs):
        return [{"workspace_id": ws, "label": label} for ws, label in pairs]

    @staticmethod
    def to_coordinator(prompts):
        return [p for p in prompts if p[1] == "coordinator"]

    @staticmethod
    def to_others(prompts):
        return [p for p in prompts if p[1] != "coordinator"]

    def alpha(self, *extra):
        return [self.agent("wa", "p1", "lead-alpha"), *extra]

    def test_roster_config_is_checked_only_by_loop(self):
        self.write_config("brief_leads = true\n")
        with patch.object(memgate, "meminfo", return_value=(9000, 16000, 0, 0)), \
             patch.object(sys, "argv", [str(SCRIPT), "gate", "0"]), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(memgate.main(), 0)
        errors = io.StringIO()
        with patch.object(memgate, "analyze") as analyze, \
             patch.object(sys, "argv", [str(SCRIPT), "loop", "coordinator"]), contextlib.redirect_stderr(errors):
            self.assertEqual(memgate.main(), 2)
        self.assertIn("brief_leads requires brief_message", errors.getvalue())
        analyze.assert_not_called()
        self.assertFalse((memgate.state_path() / "loop.lock").exists())

    def test_validate_roster(self):
        rules = self.base / "rules.md"
        rules.write_text("Shared rules\n")
        on = dataclasses.replace(self.cfg, brief_leads=True, brief_message="See {rules_file}", rules_file=str(rules))
        memgate.validate_roster(on)
        memgate.validate_roster(dataclasses.replace(on, brief_message="No placeholder", rules_file=""))
        for changes in (dict(brief_message=""), dict(brief_message="line one\nline two"),
                        dict(rules_file=""), dict(rules_file="relative.md"),
                        dict(rules_file=str(self.base / "missing.md"))):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                memgate.validate_roster(dataclasses.replace(on, **changes))
        memgate.validate_roster(dataclasses.replace(on, brief_leads=False, brief_message="", rules_file="relative.md"))

    def test_classify_leads_and_workers(self):
        agents = [self.agent("wa", "p1", "lead-alpha"), self.agent("wa", "p2", "lead-alpha-g1"),
                  self.agent("wa", "p3", "lead-beta-next"), self.agent("wa", "p4", "impl-101"),
                  self.agent("wa", "p5"), self.agent("wb", "p1"),
                  self.agent("wc", "p1"), self.agent("wc", "p2"),
                  self.agent("wd", "p1", "lead-g1"),
                  self.agent("wz", "p1", "coordinator"), self.agent("wz", "p2"),
                  self.agent("ww", "p1", "lead-ghost")]
        leads, workers = memgate.classify(agents, {"wa": "A", "wb": "B", "wc": "C", "wd": "D", "wz": "Z"},
                                          "coordinator", {})
        self.assertEqual(sorted((key, target) for key, target, _ in leads),
                         [("lead-alpha", "lead-alpha"), ("lead-g1", "lead-g1"), ("wb:p1", "wb:p1")])
        self.assertEqual(sorted((key, label) for key, _, label in workers),
                         [("wa:p4", "lead-alpha"), ("wa:p5", "lead-alpha"), ("wc:p1", "none"), ("wc:p2", "none")])

    def test_classify_keeps_recorded_sole_lead_out_of_workers(self):
        agents = [self.agent("wb", "p1"), self.agent("wb", "p2")]
        leads, workers = memgate.classify(agents, {"wb": "B"}, "coordinator", {"wb:p1": "wb:p1"})
        self.assertEqual(leads, [])
        self.assertEqual([(key, label) for key, _, label in workers], [("wb:p2", "wb:p1")])

    def test_roster_disabled_by_default(self):
        self.drive_loop([snapshot("OK")], [], 1)
        self.assertFalse((memgate.state_path() / "roster.json").exists())

    def test_first_run_seeds_without_sending(self):
        rounds = [dict(agents=self.alpha(self.agent("wa", "p2", "impl-101")), workspaces=self.spaces(("wa", "Alpha")))] * 2
        prompts = self.drive_roster(rounds, self.roster_cfg())
        self.assertEqual(prompts, [])
        ledger = self.ledger()
        self.assertEqual(sorted(ledger["seeded"]), ["leads", "workers"])
        self.assertTrue(ledger["leads"]["lead-alpha"]["seeded"])
        self.assertIn("wa:p2", ledger["workers"])
        self.assertEqual(ledger["pending"], [])

    def test_first_run_without_seed_sends_everyone_once(self):
        rounds = [dict(agents=self.alpha(self.agent("wa", "p2", "impl-101")), workspaces=self.spaces(("wa", "Alpha")))] * 3
        cfg = self.roster_cfg(seed_on_first_run=False)
        prompts = self.drive_roster(rounds, cfg)
        self.assertEqual([(i, t) for i, t, _ in self.to_others(prompts)], [(0, "lead-alpha")])
        self.assertEqual(self.to_others(prompts)[0][2], f"Read the shared memory rules: {cfg.rules_file}")
        notices = self.to_coordinator(prompts)
        self.assertEqual(len(notices), 1)
        event = Path(notices[0][2].split(": ", 1)[1]).read_text()
        self.assertIn("lead-alpha", event)
        self.assertIn("impl-101", event)

    def test_new_lead_is_briefed_once_and_reported(self):
        base = dict(agents=self.alpha(), workspaces=self.spaces(("wa", "Alpha")))
        grown = dict(agents=self.alpha(self.agent("wc", "p1", "lead-gamma")),
                     workspaces=self.spaces(("wa", "Alpha"), ("wc", "Gamma")))
        prompts = self.drive_roster([base, grown, grown], self.roster_cfg())
        self.assertEqual([(i, t) for i, t, _ in self.to_others(prompts)], [(1, "lead-gamma")])
        notices = self.to_coordinator(prompts)
        self.assertEqual(len(notices), 1)
        self.assertIn("briefed_leads=1", notices[0][2])
        self.assertIn("lead-gamma (pane wc:p1) in Gamma [wc]", Path(notices[0][2].split(": ", 1)[1]).read_text())

    def test_brief_waits_for_idle_lead_without_question(self):
        base = dict(agents=self.alpha(), workspaces=self.spaces(("wa", "Alpha")))
        spaces = self.spaces(("wa", "Alpha"), ("wc", "Gamma"))
        cases = [("working", {}, ""), ("blocked", {}, ""), ("unknown", {}, ""), ("idle", {"launch_pending": True}, ""),
                 *[("idle", {}, screen) for screen in ("Esc to cancel", "Enter to select", "Enter to confirm",
                                                       "Do you want to proceed?", "❯ 1. Yes", "")]]
        for status, extra, screen in cases:
            with self.subTest(status=status, extra=extra, screen=screen):
                lead = self.agent("wc", "p1", "lead-gamma", status=status, **extra)
                blocked = dict(agents=self.alpha(lead), workspaces=spaces, screens={"lead-gamma": screen})
                prompts = self.drive_roster([base, blocked], self.roster_cfg())
                self.assertEqual(self.to_others(prompts), [])
                self.assertNotIn("lead-gamma", self.ledger()["leads"])
        clear = dict(agents=self.alpha(self.agent("wc", "p1", "lead-gamma")), workspaces=spaces)
        waiting = dict(clear, agents=self.alpha(self.agent("wc", "p1", "lead-gamma", status="working")))
        prompts = self.drive_roster([base, waiting, clear], self.roster_cfg())
        self.assertEqual([(i, t) for i, t, _ in self.to_others(prompts)], [(2, "lead-gamma")])

    def test_failed_brief_is_retried(self):
        base = dict(agents=self.alpha(), workspaces=self.spaces(("wa", "Alpha")))
        grown = dict(agents=self.alpha(self.agent("wc", "p1", "lead-gamma")),
                     workspaces=self.spaces(("wa", "Alpha"), ("wc", "Gamma")))
        def fail_first(index, target):
            return {} if target == "lead-gamma" and index == 1 else {"success": True}
        prompts = self.drive_roster([base, grown], self.roster_cfg(), prompt_result=fail_first)
        self.assertEqual([i for i, _, _ in self.to_others(prompts)], [1])
        self.assertNotIn("lead-gamma", self.ledger()["leads"])
        prompts = self.drive_roster([base, grown, grown, grown], self.roster_cfg(), prompt_result=fail_first)
        self.assertEqual([i for i, _, _ in self.to_others(prompts)], [1, 2])
        self.assertIn("lead-gamma", self.ledger()["leads"])

    def test_unnamed_sole_agent_is_briefed_by_pane_and_rename_keeps_record(self):
        spaces = self.spaces(("wa", "Alpha"), ("wq", "Quartz"))
        base = dict(agents=self.alpha(), workspaces=spaces)
        unnamed = dict(agents=self.alpha(self.agent("wq", "p1")), workspaces=spaces)
        renamed = dict(agents=self.alpha(self.agent("wq", "p1", "lead-delta")), workspaces=spaces)
        prompts = self.drive_roster([base, unnamed, renamed], self.roster_cfg())
        self.assertEqual([(i, t) for i, t, _ in self.to_others(prompts)], [(1, "wq:p1")])
        ledger = self.ledger()
        self.assertEqual(ledger["leads"]["wq:p1"]["pane_id"], "wq:p1")
        self.assertIn("lead-delta", ledger["leads"])

    def test_same_name_in_a_new_pane_is_briefed_again(self):
        spaces = self.spaces(("wa", "Alpha"), ("wb", "Beta"))
        base = dict(agents=[self.agent("wa", "p1", "lead-x")], workspaces=spaces)
        moved = dict(agents=[self.agent("wa", "p1", "lead-x-g1"), self.agent("wa", "p4", "lead-x")], workspaces=spaces)
        prompts = self.drive_roster([base, moved, moved], self.roster_cfg())
        self.assertEqual([(i, t) for i, t, _ in self.to_others(prompts)], [(1, "lead-x")])
        self.assertEqual(self.ledger()["leads"]["lead-x"]["pane_id"], "wa:p4")

    def test_rename_in_the_same_pane_is_not_briefed_again(self):
        spaces = self.spaces(("wa", "Alpha"))
        base = dict(agents=[self.agent("wa", "p1", "lead-x")], workspaces=spaces)
        renamed = dict(agents=[self.agent("wa", "p1", "lead-y")], workspaces=spaces)
        prompts = self.drive_roster([base, renamed, renamed], self.roster_cfg())
        self.assertEqual(self.to_others(prompts), [])
        self.assertEqual(self.ledger()["leads"]["lead-y"]["pane_id"], "wa:p1")

    def test_closed_workspace_is_pruned_and_name_is_briefed_again(self):
        alpha_spaces = self.spaces(("wa", "Alpha"))
        base = dict(agents=self.alpha(), workspaces=alpha_spaces)
        opened = dict(agents=self.alpha(self.agent("wc", "p1", "lead-gamma"), self.agent("wc", "p2", "impl-201")),
                      workspaces=self.spaces(("wa", "Alpha"), ("wc", "Gamma")))
        closed = dict(agents=self.alpha(), workspaces=alpha_spaces)
        reopened = dict(agents=self.alpha(self.agent("wd", "p1", "lead-gamma")),
                        workspaces=self.spaces(("wa", "Alpha"), ("wd", "Delta")))
        self.drive_roster([base, opened, closed], self.roster_cfg())
        ledger = self.ledger()
        self.assertNotIn("lead-gamma", ledger["leads"])
        self.assertNotIn("wc:p2", ledger["workers"])
        prompts = self.drive_roster([base, opened, closed, reopened], self.roster_cfg())
        self.assertEqual([(i, t) for i, t, _ in self.to_others(prompts)], [(1, "lead-gamma"), (3, "lead-gamma")])

    def test_herdr_failure_does_not_touch_roster(self):
        good = dict(agents=self.alpha(self.agent("wa", "p2", "impl-101")), workspaces=self.spaces(("wa", "Alpha")))
        cfg = self.roster_cfg()
        self.drive_roster([dict(good, agents=None), dict(good, workspaces=None)], cfg)
        self.assertFalse((memgate.state_path() / "roster.json").exists())
        prompts = self.drive_roster([dict(good, agents=None), dict(good, workspaces=None), good], cfg)
        self.assertEqual(prompts, [])
        self.assertIn("wa:p2", self.ledger()["workers"])

    def test_absent_coordinator_skips_roster(self):
        rounds = [dict(agents=self.alpha(self.agent("wa", "p2", "impl-101")), workspaces=self.spaces(("wa", "Alpha")),
                       coordinator=None)] * 2
        prompts = self.drive_roster(rounds, self.roster_cfg(seed_on_first_run=False))
        self.assertEqual(prompts, [])
        self.assertFalse((memgate.state_path() / "roster.json").exists())

    def test_new_workers_are_batched(self):
        spaces = self.spaces(("wa", "Alpha"))
        base = dict(agents=self.alpha(), workspaces=spaces)
        first = dict(agents=self.alpha(self.agent("wa", "p2", "impl-101")), workspaces=spaces)
        both = dict(agents=self.alpha(self.agent("wa", "p2", "impl-101"), self.agent("wa", "p3", "review-102", kind="codex")),
                    workspaces=spaces)
        prompts = self.drive_roster([base, first, both, both, both], self.roster_cfg(roster_batch_seconds=60))
        notices = self.to_coordinator(prompts)
        self.assertEqual([i for i, _, _ in notices], [3])
        self.assertIn("new_workers=2", notices[0][2])
        event = Path(notices[0][2].split(": ", 1)[1]).read_text()
        self.assertIn("- impl-101 (pane wa:p2) in Alpha [wa] lead=lead-alpha kind=claude cwd=/srv/projects/demo", event)
        self.assertIn("- review-102 (pane wa:p3) in Alpha [wa] lead=lead-alpha kind=codex cwd=/srv/projects/demo", event)

    def test_worker_notice_waits_for_busy_coordinator(self):
        spaces = self.spaces(("wa", "Alpha"))
        base = dict(agents=self.alpha(), workspaces=spaces)
        grown = dict(agents=self.alpha(self.agent("wa", "p2", "impl-101")), workspaces=spaces)
        busy = [base, dict(grown, coordinator="working"), dict(grown, coordinator="working")]
        prompts = self.drive_roster(busy, self.roster_cfg())
        self.assertEqual(prompts, [])
        self.assertEqual(len(self.ledger()["pending"]), 1)
        prompts = self.drive_roster([*busy, grown], self.roster_cfg())
        self.assertEqual([(i, t) for i, t, _ in prompts], [(3, "coordinator")])
        self.assertEqual(self.ledger()["pending"], [])

    def test_roster_rides_on_memory_notification(self):
        spaces = self.spaces(("wa", "Alpha"))
        base = dict(agents=self.alpha(), workspaces=spaces)
        grown = dict(agents=self.alpha(self.agent("wa", "p2", "impl-101")), workspaces=spaces)
        cfg = self.roster_cfg(reminder_seconds=30, roster_batch_seconds=600)
        prompts = self.drive_roster([base, grown], cfg, analyze=lambda *a, **k: snapshot())
        self.assertEqual(len(prompts), 2)
        self.assertTrue(prompts[0][2].endswith("suspects=0. Read and review: " + prompts[0][2].split(": ", 1)[1]))
        self.assertNotIn("new_workers", prompts[0][2])
        self.assertIn("suspects=0 new_workers=1 briefed_leads=0. Read and review: ", prompts[1][2])
        event = Path(prompts[1][2].split(": ", 1)[1]).read_text()
        self.assertIn("Level: **WARN**", event)
        self.assertIn("## New workers", event)
        self.assertEqual(self.ledger()["pending"], [])

    def test_worker_rename_is_not_a_new_worker(self):
        spaces = self.spaces(("wa", "Alpha"))
        base = dict(agents=self.alpha(), workspaces=spaces)
        unnamed = dict(agents=self.alpha(self.agent("wa", "p3")), workspaces=spaces)
        named = dict(agents=self.alpha(self.agent("wa", "p3", "impl-101")), workspaces=spaces)
        self.drive_roster([base, unnamed, named], self.roster_cfg(roster_batch_seconds=600))
        pending = self.ledger()["pending"]
        self.assertEqual([(item["key"], item["name"]) for item in pending], [("wa:p3", "impl-101")])
        notes, counts = memgate.roster_notes(self.ledger(), named["agents"])
        self.assertIn("- impl-101 (pane wa:p3)", notes)
        self.assertEqual(counts, " new_workers=1 briefed_leads=0")

    def test_worker_with_new_name_in_same_pane_is_new(self):
        spaces = self.spaces(("wa", "Alpha"))
        base = dict(agents=self.alpha(), workspaces=spaces)
        first = dict(agents=self.alpha(self.agent("wa", "p3", "impl-101")), workspaces=spaces)
        second = dict(agents=self.alpha(self.agent("wa", "p3", "impl-103")), workspaces=spaces)
        prompts = self.drive_roster([base, first, second], self.roster_cfg())
        notices = self.to_coordinator(prompts)
        self.assertEqual(len(notices), 2)
        self.assertIn("impl-103", Path(notices[1][2].split(": ", 1)[1]).read_text())
        self.drive_roster([base, first, second], self.roster_cfg(roster_batch_seconds=600))
        self.assertEqual([item["name"] for item in self.ledger()["pending"]], ["impl-103"])

    def test_enabling_workers_later_seeds_silently(self):
        rounds = [dict(agents=self.alpha(self.agent("wa", "p2", "impl-101")), workspaces=self.spaces(("wa", "Alpha")))] * 2
        self.drive_roster(rounds, self.roster_cfg(notify_new_workers=False))
        self.assertEqual(self.ledger()["workers"], {})
        prompts = self.drive_roster(rounds, self.roster_cfg(), reset=False)
        self.assertEqual(prompts, [])
        ledger = self.ledger()
        self.assertIn("wa:p2", ledger["workers"])
        self.assertEqual(sorted(ledger["seeded"]), ["leads", "workers"])

    def test_corrupt_roster_is_set_aside_and_reseeded(self):
        state = memgate.state_path()
        state.mkdir(parents=True)
        (state / "roster.json").write_text("not json")
        rounds = [dict(agents=self.alpha(self.agent("wa", "p2", "impl-101")), workspaces=self.spaces(("wa", "Alpha")))]
        prompts = self.drive_roster(rounds, self.roster_cfg(seed_on_first_run=False), reset=False, check_err=False)
        self.assertEqual(prompts, [])
        self.assertEqual((state / "roster.json.corrupt").read_text(), "not json")
        self.assertIn("lead-alpha", self.ledger()["leads"])
        self.assertIn("moved to roster.json.corrupt", (state / "loop.err").read_text())

    def test_save_roster_is_atomic(self):
        directory = self.base / "ledger"
        directory.mkdir()
        path = directory / "roster.json"
        memgate.save_roster(path, memgate.empty_roster())
        original = path.read_text()
        with self.assertRaises(TypeError):
            memgate.save_roster(path, dict(memgate.empty_roster(), leads={"x": object()}))
        self.assertEqual(path.read_text(), original)
        self.assertEqual(list(directory.glob("*.tmp")), [])

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
