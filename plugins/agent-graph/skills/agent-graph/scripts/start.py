#!/usr/bin/env python3
"""Launch one interactive worker. No watcher, worktree, or PR operations."""

import argparse
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time


class Stop(Exception):
    def __init__(self, code, result, message):
        self.code, self.result, self.message = code, result, message


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def arguments():
    if sys.version_info < (3, 11):
        raise Stop(2, "invalid", "Python 3.11 or newer is required")
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("workspace", "cwd", "name", "kind", "brief", "report", "receipt", "run-id"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--pane")
    parser.add_argument("--timeout", type=int, default=120000, metavar="MS")
    parser.add_argument("--ready-grace", type=float, default=15, metavar="SECONDS")
    parser.add_argument("native_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.native_args[:1] == ["--"]:
        args.native_args.pop(0)
    if os.environ.get("HERDR_ENV") != "1":
        raise Stop(2, "invalid", "run inside a herdr pane (HERDR_ENV=1)")
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", args.name):
        raise Stop(2, "invalid", "name must match [a-z][a-z0-9_-]{0,31}")
    if not re.fullmatch(r"[a-z][a-z0-9_-]*", args.kind):
        raise Stop(2, "invalid", "invalid agent kind")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}", args.run_id):
        raise Stop(2, "invalid", "invalid run ID")
    if not args.workspace or args.timeout <= 0:
        raise Stop(2, "invalid", "workspace and a positive timeout are required")
    if not math.isfinite(args.ready_grace) or args.ready_grace < 0:
        raise Stop(2, "invalid", "--ready-grace must be a finite number of seconds, zero or more")
    for name in ("cwd", "brief", "report", "receipt"):
        path = Path(getattr(args, name))
        if not path.is_absolute():
            raise Stop(2, "invalid", f"--{name} must be absolute")
        setattr(args, name, str(path.resolve()))
    if not Path(args.cwd).is_dir() or not Path(args.brief).is_file():
        raise Stop(2, "invalid", "cwd must be a directory and brief must be a file")
    if len({args.brief, args.report, args.receipt}) != 3:
        raise Stop(2, "invalid", "brief, report, and receipt must be different files")
    if args.native_args[:1] == [args.kind]:
        raise Stop(2, "invalid", "do not repeat the executable after --kind")
    if any(a.split("=", 1)[0] in {"--prompt", "--prompt-interactive", "--print", "-p", "-i"}
           for a in args.native_args):
        raise Stop(2, "invalid", "initial prompts belong in --brief, not native arguments")
    return args


class Launch:
    def __init__(self, args):
        self.args = args
        self.deadline = time.monotonic() + args.timeout / 1000
        self.sha = hashlib.sha256(Path(args.brief).read_bytes()).hexdigest()
        self.identity = digest({k: v for k, v in vars(args).items() if k not in {"pane", "timeout", "ready_grace"}} | {"brief_sha256": self.sha})
        self.info = {k: getattr(args, k) for k in ("workspace", "cwd", "name", "kind", "brief", "report", "receipt", "run_id")}
        self.info.update(tab=None, pane=args.pane)
        self.log_path = Path(args.receipt + ".start.jsonl")
        self.events = []
        self.unrecognized_since = None

    def remaining(self):
        seconds = self.deadline - time.monotonic()
        if seconds <= 0:
            raise Stop(5, "instruction_unconfirmed", "overall startup deadline expired; inspect the pane before resuming")
        return seconds

    def command(self, *args, json_output=True):
        try:
            result = subprocess.run(["herdr", *args], capture_output=True, text=True,
                                    timeout=self.remaining(), check=False)
        except subprocess.TimeoutExpired:
            raise Stop(5, "unverified", "herdr call timed out; its operation may already have run")
        except OSError:
            raise Stop(5, "unverified", "cannot execute herdr")
        if result.returncode:
            try:
                code = json.loads(result.stderr or result.stdout).get("error", {}).get("code", "herdr_error")
            except (ValueError, AttributeError):
                code = "herdr_error"
            raise Stop(5, "unverified", str(code))
        if not json_output:
            return result.stdout
        try:
            value = json.loads(result.stdout)["result"]
            if not isinstance(value, dict):
                raise ValueError
            return value
        except (ValueError, KeyError, TypeError):
            raise Stop(5, "unverified", "invalid herdr response")

    def record(self, event, **fields):
        row = {"event": event, "identity": self.identity, "time": time.time(), **fields}
        with self.log_path.open("a", encoding="utf-8") as output:
            output.write(json.dumps(row, ensure_ascii=False) + "\n")
            output.flush()
            os.fsync(output.fileno())
        self.events.append(row)

    def has(self, event):
        return any(row["event"] == event for row in self.events)

    def agents(self):
        agents = self.command("agent", "list").get("agents")
        if not isinstance(agents, list) or any(not isinstance(a, dict) for a in agents):
            raise Stop(5, "unverified", "invalid agent list")
        return agents

    def observe(self):
        pane = self.command("pane", "get", self.info["pane"]).get("pane", {})
        if not isinstance(pane, dict) or not isinstance(pane.get("cwd"), str) or not pane["cwd"]:
            raise Stop(5, "unverified", "invalid pane response")
        if (pane.get("workspace_id") != self.args.workspace or
                Path(pane.get("cwd", "")).resolve() != Path(self.args.cwd)):
            raise Stop(3, "conflict", "pane workspace or cwd differs from this run")
        self.info["tab"] = pane.get("tab_id")
        agents = self.agents()
        collision = next((a for a in agents if a.get("name") == self.args.name and a.get("pane_id") != self.info["pane"]), None)
        if collision:
            raise Stop(3, "conflict", "agent name is held by another pane")
        agent = next((a for a in agents if a.get("pane_id") == self.info["pane"]), None)
        if agent:
            if not isinstance(agent.get("cwd"), str) or not agent["cwd"]:
                raise Stop(5, "unverified", "agent cwd is unavailable")
            if (agent.get("workspace_id") != self.args.workspace or agent.get("agent") != self.args.kind or
                    agent.get("name") not in (None, "", self.args.name) or
                    Path(agent.get("cwd", "")).resolve() != Path(self.args.cwd)):
                raise Stop(3, "conflict", "live agent does not match this run")
            recorded = next((e.get("session") for e in reversed(self.events) if e["event"] == "session"), None)
            if recorded is not None and recorded != self.session(agent):
                raise Stop(3, "conflict", "the pane now hosts a different agent session")
        return agent

    def session(self, agent):
        recorded = next((e.get("session") for e in reversed(self.events) if e["event"] == "session"), None)
        value = agent.get("agent_session")
        if value is not None and (not isinstance(value, dict) or
                                  (value.get("value") is not None and not isinstance(value["value"], str))):
            raise Stop(5, "unverified", "invalid harness session identity")
        if isinstance(value, dict) and value.get("value") and (not recorded or recorded.get("kind") != "process"):
            return value
        # Claude can publish its session ID only after the first prompt. Pin
        # the already-running foreground process until then, including PID reuse.
        proc = self.command("pane", "process-info", "--pane", self.info["pane"]).get("process_info", {})
        group = proc.get("foreground_process_group_id") if isinstance(proc, dict) else None
        if not isinstance(group, int) or group <= 0 or group == proc.get("shell_pid") or not isinstance(agent.get("terminal_id"), str):
            return None
        try:
            stat = Path(f"/proc/{group}/stat").read_text().rsplit(") ", 1)[1].split()
            start_time = stat[19]
        except (OSError, IndexError):
            try:
                result = subprocess.run(["ps", "-o", "lstart=", "-p", str(group)], capture_output=True, text=True, timeout=self.remaining())
            except (OSError, subprocess.TimeoutExpired):
                raise Stop(5, "unverified", "cannot confirm the startup process identity")
            start_time = result.stdout.strip() if result.returncode == 0 else ""
        if not start_time:
            return None
        fingerprint = {"kind": "process", "value": f"{agent['terminal_id']}:{group}:{start_time}"}
        if recorded == fingerprint and isinstance(value, dict) and value.get("value"):
            published = next((e.get("session") for e in reversed(self.events) if e["event"] == "published_session"), None)
            if published is not None and published != value:
                raise Stop(3, "conflict", "the harness session changed within the same process")
            if published is None:
                self.record("published_session", session=value)
        return fingerprint

    def receipt_matches(self):
        try:
            value = json.loads(Path(self.args.receipt).read_text())
            return isinstance(value, dict) and value.get("run_id") == self.args.run_id and value.get("brief_sha256") == self.sha
        except (OSError, ValueError):
            return False

    def screen(self, agent, *, readiness=True, may_wait=True):
        text = self.command("agent", "read", self.info["pane"], "--lines", "20", json_output=False)
        tail = "\n".join(line for line in text.splitlines() if line.strip())
        if (agent.get("agent_status") == "blocked" or
                re.search(r"(?im)^\s*(?:Enter to (?:select|confirm).*Esc to cancel|Esc to cancel|Do you (?:want to proceed|trust)|.*trust (?:this|the) (?:folder|directory)|.*trust the files|.*update.*(?:now|available)|❯\s*[1-9]\.)", tail)):
            raise Stop(4, "needs_confirmation", "inspect the trust, update, or permission screen; no answer was sent")
        if not readiness:
            return False
        if not tail:
            raise Stop(5, "unverified", "cannot verify an empty startup screen")
        if agent.get("agent_status") == "working":
            return False
        if agent.get("agent_status") not in {"idle", "done"}:
            raise Stop(5, "unverified", "agent readiness is unknown")
        # These input contracts have fixtures; live verification is recorded
        # separately. Do not guess input markers for additional harnesses.
        prompts = {"claude": "❯", "codex": "›"}
        marker = prompts.get(self.args.kind)
        lines = tail.splitlines()[-8:]
        footer = re.compile(r"\s*(?:[─━-].*[─━-]|⏵⏵.*|(?:personal|team|pro|max|api)\s*\|.*|\? for shortcuts.*"
                            r"|[◐◑◒◓○●]\s+\S+\s+·\s+/effort\s*|Not logged in · Run /login\s*)", re.I)
        ready = marker and any((line.strip() == marker or
                                (self.args.kind == "claude" and re.fullmatch(r'❯\s+Try "[^"\n]*"', line.strip())))
                               and all(footer.fullmatch(rest) for rest in lines[i + 1:])
                               for i, line in enumerate(lines))
        if not ready:
            if not may_wait:
                raise Stop(5, "unverified", "input screen is unrecognized; inspect it manually")
            # A startup notice or a wrapped footer can be on screen for a few
            # seconds. Read again until the grace period ends, then stop.
            now = time.monotonic()
            if self.unrecognized_since is None:
                self.unrecognized_since = now
            self.remaining()
            if now - self.unrecognized_since < self.args.ready_grace:
                return False
            raise Stop(5, "unverified", "input screen is unrecognized; inspect it manually")
        self.unrecognized_since = None
        return True

    def run(self):
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.log_path.with_suffix(self.log_path.suffix + ".lock").open("a")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Stop(3, "conflict", "this run is already being launched")
        try:
            try:
                self.events = [json.loads(line) for line in self.log_path.read_text().splitlines()]
            except FileNotFoundError:
                self.events = []
            except (ValueError, OSError):
                raise Stop(3, "conflict", "startup log is unreadable; do not repeat the launch")
            if any(not isinstance(e, dict) or e.get("identity") != self.identity or "event" not in e for e in self.events):
                raise Stop(3, "conflict", "run inputs changed or startup log is invalid")
            allowed = {"tab_attempt", "placement", "start_attempt", "session", "published_session", "prompt_attempt", "prompt_returned", "receipt_confirmed"}
            for event in self.events:
                if not isinstance(event["event"], str) or event["event"] not in allowed:
                    raise Stop(3, "conflict", "invalid startup log event")
                if event["event"] == "placement" and not all(isinstance(event.get(k), str) and event[k] for k in ("pane", "tab")):
                    raise Stop(3, "conflict", "startup log omitted placement IDs")
                if event["event"] in {"session", "published_session"} and event.get("session") is not None:
                    value = event["session"]
                    if not isinstance(value, dict) or not isinstance(value.get("value"), str) or not value["value"]:
                        raise Stop(3, "conflict", "invalid session identity in startup log")
            if Path(self.args.receipt).exists() and not self.has("prompt_attempt"):
                raise Stop(3, "conflict", "receipt exists without a recorded delivery attempt; use a new run after inspection")
            placement = next((e for e in reversed(self.events) if e["event"] == "placement"), None)
            if placement:
                if self.args.pane and self.args.pane != placement["pane"]:
                    raise Stop(3, "conflict", "explicit pane differs from the recorded run")
                self.info.update(pane=placement["pane"], tab=placement["tab"])
            elif self.has("tab_attempt"):
                raise Stop(5, "unverified", "tab creation response was lost; do not create another tab")
            elif self.args.pane:
                raise Stop(3, "conflict", "--pane resumes a recorded run; an unrecorded pane cannot be adopted")
            if not self.info["pane"]:
                if any(a.get("name") == self.args.name for a in self.agents()):
                    raise Stop(3, "conflict", "agent name is already in use; pass the recorded run's pane to resume")
                self.record("tab_attempt")
                result = self.command("tab", "create", "--workspace", self.args.workspace,
                                      "--cwd", self.args.cwd, "--label", self.args.name, "--no-focus")
                try:
                    self.info.update(pane=result["root_pane"]["pane_id"], tab=result["tab"]["tab_id"])
                except (KeyError, TypeError):
                    raise Stop(5, "unverified", "tab response omitted placement; inspect herdr before resuming")
                if not all(isinstance(self.info[k], str) and self.info[k] for k in ("pane", "tab")):
                    raise Stop(5, "unverified", "tab response has invalid placement IDs")
            if not placement:
                self.record("placement", pane=self.info["pane"], tab=self.info["tab"])
            agent = self.observe()
            if agent and not self.has("start_attempt"):
                raise Stop(3, "conflict", "pane already hosts an agent not launched by this run")
            if not agent:
                if self.has("start_attempt"):
                    raise Stop(5, "unverified", "an earlier launch may have run; no second agent was started")
                self.record("start_attempt")
                try:
                    self.command("agent", "start", self.args.name, "--kind", self.args.kind,
                                 "--pane", self.info["pane"], "--timeout", str(min(300000, max(1, math.ceil(self.remaining() * 1000)))),
                                 "--", *self.args.native_args)
                except Stop:
                    # A rejected/timed-out start can leave a live unnamed agent.
                    pass
                agent = self.observe()
                if not agent:
                    raise Stop(5, "unverified", "no matching agent observed after startup; no second start was attempted")
            old_session = next((e.get("session") for e in reversed(self.events) if e["event"] == "session"), None)
            session = self.session(agent)
            if old_session is not None and old_session != session:
                raise Stop(3, "conflict", "the pane now hosts a different agent session")
            if self.has("prompt_attempt") and old_session is None:
                raise Stop(5, "unverified", "session identity is unavailable; inspect the previous delivery manually")
            if session is None:
                self.screen(agent, may_wait=False)
                raise Stop(5, "unverified", "session identity is unavailable; no input was sent")
            if not self.has("session"):
                self.record("session", session=session)
            if not agent.get("name"):
                agent = self.observe()
                if not agent or agent.get("name") not in (None, ""):
                    raise Stop(3, "conflict", "agent changed before name repair")
                self.command("agent", "rename", self.info["pane"], self.args.name)
                agent = self.observe()
                if not agent or agent.get("name") != self.args.name:
                    raise Stop(5, "unverified", "cannot confirm repaired agent name")
            if self.receipt_matches() and self.has("prompt_attempt"):
                return
            if self.has("prompt_attempt"):
                raise Stop(5, "instruction_unconfirmed", "a previous prompt may have arrived; inspect its receipt and pane, do not resend")
            while not self.screen(agent):
                time.sleep(min(0.2, self.remaining()))
                agent = self.observe()
                if not agent:
                    raise Stop(5, "unverified", "agent disappeared before instruction delivery")
            if hashlib.sha256(Path(self.args.brief).read_bytes()).hexdigest() != self.sha:
                raise Stop(3, "conflict", "brief changed during launch")
            message = (f"Read the entire brief and follow it: {self.args.brief}\n"
                       f"Before work, compute its SHA-256 and atomically write a JSON receipt to {self.args.receipt} "
                       f'with keys "run_id" (value {json.dumps(self.args.run_id)}) and "brief_sha256". '
                       f"Write to a temporary file beside the receipt, then rename it. Report to {self.args.report}. "
                       "A receipt acknowledges instructions; it does not mean the task is complete.")
            self.record("prompt_attempt")
            agent = self.observe()
            if not agent or not self.screen(agent, may_wait=False):
                raise Stop(5, "unverified", "agent changed before instruction delivery; inspect before resuming")
            try:
                self.command("agent", "prompt", self.info["pane"], message)
                self.record("prompt_returned")
            except Stop:
                # Delivery can have succeeded even if the reply was lost.
                pass
            while True:
                self.remaining()
                agent = self.observe()
                if not agent or (session is not None and self.session(agent) != session):
                    raise Stop(5, "unverified", "agent session disappeared or changed while awaiting receipt")
                if self.receipt_matches():
                    self.record("receipt_confirmed")
                    return
                self.screen(agent, readiness=False)
                time.sleep(min(0.2, self.remaining()))
        finally:
            lock.close()


def main():
    launch = None
    try:
        launch = Launch(arguments())
        launch.run()
        value = {"result": "started", **launch.info}
        code = 0
    except Stop as error:
        value = {"result": error.result, "message": error.message, **(launch.info if launch else {})}
        code = error.code
        if launch and launch.info["pane"]:
            try:
                budget = launch.deadline - time.monotonic()
                if budget > 0:
                    subprocess.run(["herdr", "notification", "show", f"{launch.args.name}: 起動の確認が必要です",
                                    "--body", f"{error.message}; pane={launch.info['pane']}"],
                                   capture_output=True, timeout=min(2, budget), check=False)
            except (OSError, subprocess.TimeoutExpired):
                pass
        print(f"agent-graph start: {error.message}", file=sys.stderr)
    except (OSError, ValueError) as error:
        value = {"result": "unverified", "message": type(error).__name__, **(launch.info if launch else {})}
        code = 5
        print(f"agent-graph start: {type(error).__name__}; inspect artifacts before resuming", file=sys.stderr)
    print(json.dumps(value, ensure_ascii=False))
    return code


if __name__ == "__main__":
    sys.exit(main())
