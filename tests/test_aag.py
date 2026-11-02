"""Your agent's tasks on the list (aag.py): where each goes, what stays yours, what the refresh leaves
alone, and reading aag here or over SSH."""
import subprocess
import unittest
from unittest import mock

from docket import aag, app, store, tasks

AT = "2026-03-02T12:00:00Z"
DAY, NOW = aag._local(AT)[:10], aag._local(AT)  # the day it is here when aag says AT


def a(id, column, next="Say go: kalman asked", **kw):
    """A task as `aag status --json` has it."""
    return {"id": id, "title": f"Task {id}", "column": column, "next": next, "requestedBy": "kalman",
            "status": "done" if column == "done" else "waiting", "phase": None,
            "line": "review · CI ✅ · waiting on you: your go (kalman asked) (2h)",
            "dm": "https://x.slack.com/archives/D1/p100", "thread": "https://x.slack.com/archives/C9/p200",
            "pr": {"key": f"o/r#{id}", "url": f"https://github.com/o/r/pull/{id}"}, "statusAt": AT, "updatedAt": AT, **kw}


def feed(*items, **kw):
    return {"v": 1, "me": "valera", "dm": "D1", "workdir": "/Users/v/aag-work", "daemon": "running", "host": "mac",
            "tasks": list(items), **kw}


def synced(*items):
    reg = {"tasks": []}
    aag.sync(reg, feed(*items), DAY, NOW)
    return reg, {t["id"]: t for t in reg["tasks"]}


class Mapping(unittest.TestCase):
    def test_your_move_is_open_and_links_the_dm_and_the_pr(self):
        _, by = synced(a("1", "needs_you"))
        t = by["aag-1"]
        self.assertEqual((t["status"], t["title"], t["next"], t["who"], t["do"]), ("open", "Task 1", "Say go: kalman asked", "kalman", DAY))
        self.assertEqual([s["link"] for s in t["sources"]], ["https://x.slack.com/archives/D1/p100", "https://github.com/o/r/pull/1",
                                                            "https://x.slack.com/archives/C9/p200"])
        self.assertEqual(t["why"], "review · CI ✅ · waiting on you: your go (kalman asked)")  # its age isn't news
        self.assertIn(t, tasks.order(list(by.values()), DAY))

    def test_working_or_waiting_on_others_is_waiting_and_done_is_handled_today(self):
        _, by = synced(a("1", "working", "Bash: npm test"), a("2", "waiting", "Reviews from kalman"), a("3", "done", "Merged"))
        self.assertEqual([by[k]["status"] for k in ("aag-1", "aag-2", "aag-3")], ["waiting", "waiting", "done"])
        self.assertEqual((by["aag-3"]["done_at"], by["aag-3"]["closed_by"]), (NOW, "aag"))

    def test_done_before_today_isnt_added_and_a_task_leaves_when_aag_stops_listing_it(self):
        reg, by = synced(a("1", "needs_you"), a("2", "done", status="done", statusAt="2026-02-20T12:00:00Z"))
        self.assertEqual(list(by), ["aag-1"])
        mine = tasks._new({"title": "Write the launch post"}, DAY, NOW)
        reg["tasks"].append(mine)
        self.assertTrue(aag.sync(reg, feed(), DAY, NOW))
        self.assertEqual(reg["tasks"], [mine])  # yours stay
        self.assertFalse(aag.sync(reg, feed(), DAY, NOW))

    def test_your_own_task_waits_for_nobody_and_your_review_is_deep_work(self):
        _, by = synced(a("1", "needs_you", "Your review", requestedBy="valera", phase="owner_review"))
        self.assertEqual((by["aag-1"]["who"], by["aag-1"]["kind"]), ("", "deep"))


class Yours(unittest.TestCase):
    def setUp(self):
        tasks.save(synced(a("1", "needs_you"))[0])

    def test_your_snooze_and_priority_stay(self):
        tasks.act("aag-1", "snooze", "2026-12-01", DAY, NOW)
        reg = tasks.act("aag-1", "priority", "P0", DAY, NOW)
        aag.sync(reg, feed(a("1", "needs_you", "Merge it")), DAY, NOW)
        t = reg["tasks"][0]
        self.assertEqual((t["do"], t["priority"], t["next"]), ("2026-12-01", "P0", "Merge it"))

    def test_your_done_stays_until_the_next_move_changes(self):
        reg = tasks.act("aag-1", "done", None, DAY, NOW)
        aag.sync(reg, feed(a("1", "needs_you")), DAY, NOW)
        self.assertEqual(reg["tasks"][0]["status"], "done")
        aag.sync(reg, feed(a("1", "needs_you", "Merge it")), DAY, NOW)
        self.assertEqual((reg["tasks"][0]["status"], reg["tasks"][0]["done_at"]), ("open", None))


class Refresh(unittest.TestCase):
    """The hourly refresh leaves aag's tasks to aag, and doesn't add what they cover."""

    def test_claude_changes_nothing_on_an_aag_task(self):
        reg, by = synced(a("1", "needs_you"))
        with mock.patch.object(tasks, "extra", aag.extra):
            tasks.merge(reg, {"updates": [{"id": "aag-1", "title": "Reply go", "status": "done", "evidence": "said go",
                                           "sources": [{"label": "x", "link": "https://x.slack.com/archives/D1/p300"}]}]}, DAY, NOW)
            t = reg["tasks"][0]
            self.assertEqual((t["title"], t["status"], len(t["sources"])), ("Task 1", "open", 3))
            other = tasks._new({"title": "Reply to Alex"}, DAY, NOW)
            self.assertNotIn("aag", other)

    def test_the_rule_names_the_bot_dm_and_the_prs(self):
        rule = aag.rules(feed(a("7", "waiting"), a("8", "done")))
        self.assertIn("channel D1", rule)
        self.assertIn("o/r#7, o/r#8", rule)
        self.assertIn("Sam's agent", rule.format(who="Sam"))

    def test_a_task_about_aags_work_that_the_refresh_adds_anyway_comes_off(self):
        tasks.save(synced(a("7", "needs_you"))[0])
        with mock.patch.object(aag, "_feed", {"feed": feed(a("7", "needs_you"))}), mock.patch.object(aag, "_known", {"aag-7"}):
            reg = tasks.load()
            tasks.merge(reg, {"new": [{"title": "Say go to your agent", "sources": [{"label": "DM", "link": "https://x.slack.com/archives/D1/p900"}]},
                                      {"title": "Look at the PR's tests", "sources": [{"label": "PR", "link": "https://github.com/o/r/pull/7/files"}]},
                                      {"title": "Reply to Alex", "sources": [{"label": "DM", "link": "https://x.slack.com/archives/D2/p1"}]}]}, DAY, NOW)
            tasks.save(reg)
            aag.after_refresh(reg)
        self.assertEqual(sorted(t["title"] for t in tasks.load()["tasks"]), ["Reply to Alex", "Task 7"])


class Reading(unittest.TestCase):
    def setUp(self):
        self.settings = store.settings()

    def tearDown(self):
        store.write(store.SETTINGS, self.settings)
        aag._feed.update(feed=None, at=None, error=None, since=None)
        app.RULES = aag._rules

    def test_here_or_over_ssh(self):
        with mock.patch.object(app, "_which", return_value="/u/bin/aag"):
            self.assertEqual(aag.command("local"), ["/u/bin/aag", "status", "--json"])
        self.assertEqual(aag.command("ssh vps"), ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "--", "vps", "aag", "status", "--json"])
        self.assertEqual(aag.command("ssh vps ~/auto-agents/aag")[-3:], ["~/auto-agents/aag", "status", "--json"])
        self.assertIsNone(aag.command(None))
        with self.assertRaises(RuntimeError):
            aag.command("vps")

    def test_an_older_aag_or_a_failed_run_is_an_error(self):
        for out in (subprocess.CompletedProcess([], 0, "UP (pid 1). Turn off: aag stop.\n", ""),
                    subprocess.CompletedProcess([], 255, "", "ssh: connect to host vps: Connection refused")):
            with mock.patch.object(aag.subprocess, "run", return_value=out), self.assertRaises(RuntimeError):
                aag.read("ssh vps")

    def test_a_failed_read_keeps_the_last_tasks_and_says_since_when(self):
        tasks.save({"tasks": []})
        store.write(store.SETTINGS, {**self.settings, "aag": "local"})
        with mock.patch.object(aag, "read", return_value=feed(a("1", "needs_you"))):
            aag.poll()
        self.assertEqual([t["id"] for t in tasks.load()["tasks"]], ["aag-1"])
        self.assertEqual(store.settings()["agent_paths"], ["/Users/v/aag-work"])  # its sessions are agents'
        self.assertIn("o/r#1", app.RULES)
        with mock.patch.object(aag, "read", side_effect=RuntimeError("timed out")):
            aag.poll()
            first = aag.state()["aag"]["since"]
            aag.poll()
        s = aag.state()["aag"]
        self.assertEqual((s["error"], s["since"], s["feed"]["tasks"][0]["id"], s["where"]), ("timed out", first, "1", "local"))
        self.assertEqual([t["id"] for t in tasks.load()["tasks"]], ["aag-1"])

    def test_without_the_setting_nothing_shows_and_its_tasks_go(self):
        tasks.save(synced(a("1", "needs_you"))[0])
        store.write(store.SETTINGS, {k: v for k, v in self.settings.items() if k != "aag"})
        aag.poll()
        self.assertEqual((aag.state(), tasks.load()["tasks"]), ({}, []))
