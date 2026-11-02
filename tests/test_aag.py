"""Your agent's tasks on the list (aag.py): where each goes, what stays yours, how aag's feed and the
refresh's reading of the agent's DM threads make one list, and reading aag here or over SSH."""
import subprocess
import time
import unittest
from unittest import mock

from docket import aag, app, store, tasks

AT = "2026-03-02T12:00:00Z"
DAY, NOW = aag._local(AT)[:10], aag._local(AT)  # the day it is here when aag says AT


def dm(id, reply=False, host="x"):
    """A link into task `id`'s DM thread: its start, or a reply in it; on one of the workspace's hosts."""
    return (f"https://{host}.slack.com/archives/D1/p1730000099000000?thread_ts=1730000000.{int(id):06d}&cid=D1" if reply
            else f"https://{host}.slack.com/archives/D1/p1730000000{int(id):06d}")


def a(id, column, next="Say go: kalman asked", **kw):
    """A task as `aag status --json` has it."""
    return {"id": id, "title": f"Task {id}", "column": column, "next": next, "requestedBy": "kalman",
            "status": "done" if column == "done" else "waiting", "phase": None,
            "line": "review · CI ✅ · waiting on you: your go (kalman asked) (2h)", "dm": dm(id),
            "thread": f"https://x.slack.com/archives/C9/p1730000001{int(id):06d}",
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
        self.assertEqual([s["link"] for s in t["sources"]], [dm(1), "https://github.com/o/r/pull/1", "https://x.slack.com/archives/C9/p1730000001000001"])
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

    def test_a_review_the_agents_agree_on_is_handled_unless_it_needs_your_approval(self):
        reviewed = dict(role="reviewer", status="agents_done")
        reg, by = synced(a("1", "waiting", "Reviewed; the merge is up to humans", **reviewed),
                         a("2", "waiting", "Reviewed; kalman asks for the human review next", **reviewed, statusAt="2026-02-20T12:00:00Z"),
                         a("3", "needs_you", "Your approval: the agents agree", **reviewed))
        self.assertEqual(sorted(by), ["aag-1", "aag-3"], "one reviewed before today isn't added")
        self.assertEqual((by["aag-1"]["status"], by["aag-1"]["done_at"], by["aag-3"]["status"]), ("done", NOW, "open"))
        aag.sync(reg, feed(a("1", "waiting", "Fixes from kalman", role="reviewer")), DAY, NOW)  # new changes to review
        self.assertEqual((reg["tasks"][0]["status"], reg["tasks"][0]["done_at"]), ("waiting", None))

    def test_your_own_task_waits_for_nobody_and_your_review_is_deep_work(self):
        _, by = synced(a("1", "needs_you", "Your review", requestedBy="valera", phase="owner_review"))
        self.assertEqual((by["aag-1"]["who"], by["aag-1"]["kind"]), ("", "deep"))

    def test_one_thread_whatever_the_host_name_and_a_reply_is_in_its_thread(self):
        self.assertEqual(aag.thread(dm(1)), ("D1", "1730000000.000001"))
        self.assertEqual(aag.thread(dm(1, reply=True, host="x.enterprise")), ("D1", "1730000000.000001"))
        self.assertIsNone(aag.thread("https://github.com/o/r/pull/1"))


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


class Reconcile(unittest.TestCase):
    """aag's feed and the refresh's reading of the DM threads: one task per thread."""

    def setUp(self):
        aag._feed.update(feed=feed(), at=NOW, error=None, since=None)  # the feed can be read

    def tearDown(self):
        aag._feed.update(feed=None, at=None, error=None, since=None)

    def update(self, reg, **u):
        with mock.patch.object(tasks, "extra", aag.extra):
            tasks.merge(reg, {"updates": [{"id": reg["tasks"][0]["id"], **u}]}, DAY, NOW)
        return reg["tasks"][0]

    def test_while_the_feed_is_read_the_refresh_adds_what_the_thread_says_but_not_its_state(self):
        reg, _ = synced(a("1", "needs_you"))
        t = self.update(reg, title="Reply go", status="done", evidence="said go", why="Kalman wants the v36 frame gone first",
                        sources=[{"label": "the ask", "link": dm(1, reply=True)}, {"label": "doc", "link": "https://notion.so/x"}])
        self.assertEqual((t["title"], t["status"], t["why"], len(t["sources"])), ("Task 1", "open", "Kalman wants the v36 frame gone first", 5))
        aag.sync(reg, feed(a("1", "needs_you", "Merge it")), DAY, NOW)
        self.assertEqual((t["next"], t["why"], len(t["sources"])), ("Merge it", "Kalman wants the v36 frame gone first", 5))

    def test_while_it_cant_be_read_the_refresh_has_the_say(self):
        reg, _ = synced(a("1", "needs_you"))
        aag._feed.update(error="timed out")
        t = self.update(reg, status="done", evidence="the agent says it merged")
        self.assertEqual(t["status"], "done")

    def test_a_task_the_refresh_made_from_a_thread_becomes_that_threads_aag_task(self):
        made = tasks._new({"title": "Say go to your agent for #1", "why": "Kalman asked for it",
                           "sources": [{"label": "slack DM with Valera's agent", "link": dm(1, host="x.enterprise")}]}, DAY, NOW)
        made.update(priority="P0", mine=["priority"])
        reg = {"tasks": [made]}
        aag.sync(reg, feed(a("1", "needs_you")), DAY, NOW)
        [t] = reg["tasks"]
        self.assertEqual((t["id"], t["aag"]["id"], t["title"], t["priority"], t["why"]),
                         (made["id"], "1", "Task 1", "P0", "Kalman asked for it"))
        self.assertEqual(len(t["sources"]), 3, "the same message on another host is one link")

    def test_the_rule_lists_the_agents_threads_and_says_whether_the_feed_has_the_say(self):
        reg, _ = synced(a("7", "waiting"), a("8", "needs_you"), a("9", "done"))
        rule = aag.rules(reg).format(who="Sam")
        self.assertIn("\"Sam's agent\"", rule)
        self.assertIn("DM channel D1", rule)
        self.assertIn(f"aag-7 (DM thread {dm(7)}, o/r#7, agents thread", rule)
        self.assertNotIn("aag-9", rule)
        self.assertIn("leave those out", rule)
        aag._feed.update(error="timed out")
        self.assertIn("can't be read", aag.rules(reg))
        self.assertNotIn("one per thread", aag.rules({"tasks": []}))

    def test_what_the_refresh_adds_anyway_is_folded_in(self):
        tasks.save(synced(a("7", "needs_you"))[0])
        reg = tasks.load()
        new = [("Say go to your agent", "Kalman wants v36 cleaned up first", dm(7, reply=True)),
               ("Look at the PR's tests", "", "https://github.com/o/r/pull/7/files"),
               ("Your agent is back online", "", "https://x.slack.com/archives/D1/p1730000555000000"),
               ("Reply to Alex", "", "https://x.slack.com/archives/D2/p1730000000000001")]
        tasks.merge(reg, {"new": [{"title": t, "why": w, "sources": [{"label": "x", "link": x}]} for t, w, x in new]}, DAY, NOW)
        tasks.save(reg)
        with mock.patch.object(aag, "_known", {"aag-7"}):
            aag.after_refresh(reg)
        by = {t["title"]: t for t in tasks.load()["tasks"]}
        self.assertEqual(sorted(by), ["Reply to Alex", "Task 7"], "the notice too, while the feed is read")
        self.assertEqual(by["Task 7"]["why"], "Kalman wants v36 cleaned up first")
        self.assertIn(dm(7, reply=True), [s["link"] for s in by["Task 7"]["sources"]])


class Reading(unittest.TestCase):
    def setUp(self):
        self.settings = store.settings()

    def tearDown(self):
        store.write(store.SETTINGS, self.settings)
        aag._feed.update(feed=None, at=None, error=None, since=None)
        app.RULES = aag._rules

    def test_here_by_itself_or_over_ssh(self):
        with mock.patch.object(app, "_which", return_value="/u/bin/aag"):
            self.assertEqual([aag.command(None), aag.command("local")], [["/u/bin/aag", "status", "--json"]] * 2)
        with mock.patch.object(app, "_which", return_value=None):
            self.assertIsNone(aag.command(None))  # no aag here: nothing to read, nothing to say
            with self.assertRaises(RuntimeError):
                aag.command("local")
        self.assertEqual(aag.command("ssh vps"), ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "--", "vps", "aag", "status", "--json"])
        self.assertEqual(aag.command("ssh vps ~/auto-agents/aag")[-3:], ["~/auto-agents/aag", "status", "--json"])
        self.assertIsNone(aag.command("off"))
        with self.assertRaises(RuntimeError):
            aag.command("vps")

    def test_an_older_aag_or_a_failed_run_is_an_error(self):
        for out in (subprocess.CompletedProcess([], 0, "UP (pid 1). Turn off: aag stop.\n", ""),
                    subprocess.CompletedProcess([], 255, "", "ssh: connect to host vps: Connection refused")):
            with mock.patch.object(aag.subprocess, "run", return_value=out), self.assertRaises(RuntimeError):
                aag.read(["ssh", "vps"])

    def test_a_failed_read_keeps_the_last_tasks_and_says_since_when(self):
        tasks.save({"tasks": []})
        store.write(store.SETTINGS, {k: v for k, v in self.settings.items() if k not in ("aag", "agent_paths")})
        with mock.patch.object(app, "_which", return_value="/u/bin/aag"), mock.patch.object(aag, "read", return_value=feed(a("1", "needs_you"))):
            aag.poll()
        self.assertEqual([t["id"] for t in tasks.load()["tasks"]], ["aag-1"])
        self.assertEqual(store.settings()["agent_paths"], ["/Users/v/aag-work"])  # its sessions are agents'
        self.assertIn("aag-1 (DM thread", app.RULES)
        with mock.patch.object(app, "_which", return_value="/u/bin/aag"), mock.patch.object(aag, "read", side_effect=RuntimeError("timed out")):
            aag.poll()
            first = aag.state()["aag"]["since"]
            aag.poll()
        s = aag.state()["aag"]
        self.assertEqual((s["error"], s["since"], s["feed"]["tasks"][0]["id"], s["where"]), ("timed out", first, "1", "local"))
        self.assertEqual([t["id"] for t in tasks.load()["tasks"]], ["aag-1"])
        self.assertIn("can't be read", app.RULES)

    def test_with_no_aag_here_the_refresh_still_reads_the_agents_dms(self):
        tasks.save(synced(a("1", "needs_you"))[0])
        store.write(store.SETTINGS, {k: v for k, v in self.settings.items() if k != "aag"})
        with mock.patch.object(app, "_which", return_value=None):
            aag.poll()
        self.assertEqual((aag.state(), [t["id"] for t in tasks.load()["tasks"]]), ({}, ["aag-1"]))
        self.assertIn("{who}'s agent", app.RULES)

    def test_off_takes_its_tasks_and_its_rule_away(self):
        tasks.save(synced(a("1", "needs_you"))[0])
        store.write(store.SETTINGS, {**self.settings, "aag": "off"})
        aag.poll()
        self.assertEqual((aag.state(), tasks.load()["tasks"], app.RULES), ({}, [], aag._rules))

    def test_docket_run_goes_on_once_aag_was_read_so_the_first_refresh_knows_what_it_has(self):
        read = []
        with mock.patch.object(aag, "poll", lambda: (time.sleep(0.2), read.append(1))), mock.patch.object(aag, "EVERY", 3600), \
             mock.patch.object(app, "STATE", dict), mock.patch.object(app, "AFTER_REFRESH", []), mock.patch.object(tasks, "extra", tasks.extra):
            aag.start()
            self.assertEqual(read, [1])
            self.assertIn(aag.after_refresh, app.AFTER_REFRESH)
