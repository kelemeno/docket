"""The task list's rules, your notes to Claude, the PR panel, and the local page's guards."""
import json
import os
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from unittest import mock

from docket import app, store, tasks

DAY, NOW = "2026-03-02", "2026-03-02T11:00:00"


def task(**kw):
    return tasks._new({"title": "Merge the release-notes PR", "next": "Mark it ready", "kind": "quick",
                       "priority": "P2", **kw}, DAY, NOW)


class Rules(unittest.TestCase):
    def setUp(self):
        self.t = task()
        tasks.save({"tasks": [self.t]})

    def test_a_field_you_set_stays_yours(self):
        tasks.act(self.t["id"], "priority", "P2", DAY, NOW)
        reg = tasks.load()
        tasks.merge(reg, {"updates": [{"id": self.t["id"], "priority": "P0", "next": "Ping Alex"}]}, DAY, NOW)
        self.assertEqual((reg["tasks"][0]["priority"], reg["tasks"][0]["next"]), ("P2", "Ping Alex"))

    def test_done_needs_evidence(self):
        reg = tasks.load()
        tasks.merge(reg, {"updates": [{"id": self.t["id"], "status": "done"}]}, DAY, NOW)
        self.assertEqual(reg["tasks"][0]["status"], "open")
        tasks.merge(reg, {"updates": [{"id": self.t["id"], "status": "done", "evidence": "merged"}]}, DAY, NOW)
        self.assertEqual(reg["tasks"][0]["status"], "done")

    def test_a_dropped_task_doesnt_come_back(self):
        reg = tasks.act(self.t["id"], "drop", None, DAY, NOW)
        tasks.merge(reg, {"new": [{"title": "merge the release-notes PR"}]}, DAY, NOW)
        self.assertEqual(len(reg["tasks"]), 1)

    def test_an_overdue_task_rolls_to_today_and_counts(self):
        reg = {"tasks": [task()]}
        reg["tasks"][0]["do"] = "2026-03-01"
        tasks.roll(reg, DAY)
        self.assertEqual((reg["tasks"][0]["do"], reg["tasks"][0]["rolled"]), (DAY, 1))
        self.assertFalse(tasks.roll(reg, DAY))  # once a day

    def test_only_web_links(self):
        t = task(sources=[{"label": "x", "link": "javascript:alert(1)"}, {"label": "y", "link": "https://example.com/a"}])
        self.assertEqual([s["link"] for s in t["sources"]], ["", "https://example.com/a"])


class Notes(unittest.TestCase):
    def setUp(self):
        self.t = task()
        tasks.save({"tasks": [self.t]})

    def answer(self, out):
        return next(t for t in tasks.answer(self.t["id"], out, DAY, NOW)["tasks"] if t["id"] == self.t["id"])

    def test_a_note_waits_for_an_answer(self):
        t = tasks.note(self.t["id"], "  ask Sam for a review   in the team thread ", NOW)
        self.assertEqual(t["notes"][-1], {"at": NOW, "by": "you", "text": "ask Sam for a review in the team thread"})
        self.assertEqual(t["asked"], NOW)

    def test_an_empty_note_or_a_missing_task_is_refused(self):
        with self.assertRaises(ValueError):
            tasks.note(self.t["id"], "  ", NOW)
        with self.assertRaises(KeyError):
            tasks.note("nope", "hi", NOW)

    def test_the_answer_changes_the_task_and_goes_under_the_note(self):
        tasks.note(self.t["id"], "ask Sam for a review in the team thread", NOW)
        t = self.answer({"update": {"next": "Ask Sam for a review in the #team thread", "who": "Sam",
                                    "sources": [{"label": "slack thread", "link": "https://example.slack.com/archives/C1/p1"}]},
                         "answer": "Found the thread and linked it."})
        self.assertEqual((t["next"], t["who"]), ("Ask Sam for a review in the #team thread", "Sam"))
        self.assertIn("https://example.slack.com/archives/C1/p1", [s["link"] for s in t["sources"]])
        self.assertEqual(t["notes"][-1]["by"], "claude")
        self.assertNotIn("asked", t)

    def test_your_note_wins_over_fields_you_set(self):
        tasks.act(self.t["id"], "priority", "P2", DAY, NOW)
        self.assertEqual(self.answer({"update": {"priority": "P0"}, "answer": "Raised it."})["priority"], "P0")

    def test_done_from_a_note_still_needs_evidence(self):
        self.assertEqual(self.answer({"update": {"status": "done"}})["status"], "open")
        self.assertEqual(self.answer({"update": {"status": "done", "evidence": "merged"}})["status"], "done")

    def test_a_new_task_from_a_note_isnt_a_duplicate(self):
        reg = tasks.answer(self.t["id"], {"new": [{"title": "Ping Sam"}, {"title": self.t["title"]}], "answer": "Added."}, DAY, NOW)
        self.assertEqual([t["title"] for t in reg["tasks"]], [self.t["title"], "Ping Sam"])

    def test_when_claude_fails_the_page_stops_waiting(self):
        tasks.note(self.t["id"], "hi", NOW)
        tasks.unasked(self.t["id"], "claude failed", NOW)
        t = tasks.load()["tasks"][0]
        self.assertNotIn("asked", t)
        self.assertIn("claude failed", t["notes"][-1]["text"])

    def test_the_hourly_refresh_reads_your_notes(self):
        tasks.note(self.t["id"], "ask Sam", NOW)
        with mock.patch.object(app, "_ask", return_value={}) as ask, mock.patch.object(app.sessions, "recent", return_value=[]):
            app.refresh()
        self.assertIn("you: ask Sam", ask.call_args[0][0])
        self.assertFalse(ask.call_args.kwargs.get("act"))  # the refresh never acts

    def test_a_note_is_acted_on_unless_settings_say_act_false(self):
        class Now:  # the page answers in a thread; here at once
            def __init__(self, target, daemon): self.target = target
            def start(self): self.target()
        for settings, act in (({}, True), ({"act": False}, False)):
            with mock.patch.object(app, "_ask", return_value={"answer": "Sent."}) as ask, \
                 mock.patch.object(app.threading, "Thread", Now), mock.patch.object(store, "settings", return_value=settings):
                app.Page().reply(self.t["id"], "text Sam that it's merged")
            self.assertEqual(ask.call_args.kwargs["act"], act)
            self.assertEqual("as me: what the" in ask.call_args[0][0], act)
            self.assertEqual("You can only read" in ask.call_args[0][0], not act)


class Snooze(unittest.TestCase):
    def setUp(self):
        self.t = task()
        tasks.save({"tasks": [self.t]})

    def test_for_half_an_hour_while_an_agent_runs(self):
        t = tasks.act(self.t["id"], "snooze", "2026-03-02T11:30", DAY, NOW)["tasks"][0]
        self.assertEqual((t["do"], t["until"]), (DAY, "2026-03-02T11:30"))
        t = tasks.act(self.t["id"], "snooze", "2026-03-03", DAY, NOW)["tasks"][0]
        self.assertEqual(t["do"], "2026-03-03")
        self.assertNotIn("until", t)

    def test_not_into_the_past(self):
        for bad in ("2026-03-02T10:59", "2026-03-01", "11:30", "2026-03-02T25:00"):
            with self.assertRaises(ValueError):
                tasks.act(self.t["id"], "snooze", bad, DAY, NOW)

    def test_reopening_ends_the_snooze(self):
        tasks.act(self.t["id"], "snooze", "2026-03-02T11:30", DAY, NOW)
        self.assertNotIn("until", tasks.act(self.t["id"], "reopen", None, DAY, NOW)["tasks"][0])


class Meetings(unittest.TestCase):
    def test_calendar_times_become_this_computers_time(self):
        with mock.patch.dict(os.environ, {"TZ": "Asia/Tokyo"}):
            time.tzset()
            try:
                # a meeting on another time zone's calendar: 14:20 UTC is 23:20 in Tokyo
                m = tasks.meetings([{"start": "2026-03-02T14:20:00Z", "end": "2026-03-02T15:40:00+01:00", "title": "Standup"},
                                    {"start": "9:30", "end": "09:55", "title": "Sync"},
                                    {"start": "soon", "end": "10:00", "title": "dropped"}])
            finally:
                os.environ.pop("TZ", None)
                time.tzset()
        self.assertEqual([(x["start"], x["end"]) for x in m], [("09:30", "09:55"), ("23:20", "23:40")])


class Tools(unittest.TestCase):
    def test_only_tools_that_only_read(self):
        ok = ["mcp__x__slack_read_channel", "mcp__x__list_events", "mcp__x__notion-search", "mcp__x__get_issue"]
        no = ["mcp__x__slack_send_message", "mcp__x__create_event", "mcp__x__search_and_replace", "mcp__x__list_and_delete",
              "mcp__x__mark_read", "mcp__x__whoami"]
        self.assertEqual([t for t in ok + no if app.reads_only(t)], ok)

    def ran(self, act):
        seen = {}

        def run(cmd, **kw):
            seen.update(allow=cmd[cmd.index("--allowedTools") + 1].split(","), deny=cmd[cmd.index("--disallowedTools") + 1].split(","))
            return mock.Mock(stdout=json.dumps({"result": "{}"}), stderr="", returncode=0)
        s = {"tools": ["mcp__x__slack_read_channel"], "refused": ["mcp__x__slack_send_message", "mcp__x__save_comment",
                                                                   "mcp__x__notion-delete-pages", "mcp__x__share_file"]}
        with mock.patch.object(store, "settings", return_value=s), mock.patch.object(app, "_which", return_value="/bin/x"), \
             mock.patch("subprocess.run", run):
            app._ask("hi", act=act)
        return seen

    def test_a_refresh_only_reads_and_a_note_may_send_but_never_delete_or_share(self):
        read = self.ran(False)
        self.assertNotIn("mcp__x__slack_send_message", read["allow"])
        self.assertIn("mcp__x__slack_send_message", read["deny"])
        self.assertNotIn("Bash(gh pr comment:*)", read["allow"])
        note = self.ran(True)
        for t in ("mcp__x__slack_send_message", "mcp__x__save_comment", "Bash(gh pr comment:*)", "mcp__x__slack_read_channel"):
            self.assertIn(t, note["allow"])
        for t in ("mcp__x__notion-delete-pages", "mcp__x__share_file", "Edit", "WebFetch"):
            self.assertIn(t, note["deny"])
            self.assertNotIn(t, note["allow"])


class Prs(unittest.TestCase):
    def pr(self, **kw):
        return {"isDraft": False, "mergeable": "MERGEABLE", "reviewDecision": None,
                "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": "SUCCESS"}}}]}, **kw}

    def test_the_most_pressing_state_of_your_own(self):
        s = app._pr_state
        self.assertEqual(s(self.pr(isDraft=True, mergeable="CONFLICTING")), "draft")
        self.assertEqual(s(self.pr(mergeable="CONFLICTING")), "conflicts")
        self.assertEqual(s(self.pr(commits={"nodes": [{"commit": {"statusCheckRollup": {"state": "FAILURE"}}}]})), "CI failing")
        self.assertEqual(s(self.pr(reviewDecision="REVIEW_REQUIRED")), "needs review")
        self.assertEqual(s(self.pr(reviewDecision="APPROVED")), "ready")
        self.assertEqual(s(self.pr(commits={"nodes": []})), "ready")

    def reviewed(self, reviews=(), asked=(), pushed="2026-03-02T10:00:00Z"):
        return {"latestReviews": {"nodes": [{"author": {"login": who}, "state": st, "submittedAt": at} for who, st, at in reviews]},
                "reviewRequests": {"nodes": [{"requestedReviewer": {"login": who}} for who in asked]},
                "commits": {"nodes": [{"commit": {"committedDate": pushed}}]}}

    def test_where_one_you_review_stands_from_your_side(self):
        s = lambda **kw: app._review_state(self.reviewed(**kw), "me")
        self.assertEqual(s(asked=["me", "sam"]), "review requested")
        self.assertEqual(s(asked=["me"], reviews=[("me", "COMMENTED", "2026-03-01T09:00:00Z")]), "re-requested")
        self.assertEqual(s(reviews=[("me", "COMMENTED", "2026-03-01T09:00:00Z")]), "new commits")
        self.assertEqual(s(reviews=[("me", "CHANGES_REQUESTED", "2026-03-02T11:00:00Z")]), "waiting on author")
        self.assertEqual(s(reviews=[("me", "APPROVED", "2026-03-02T11:00:00Z"), ("sam", "COMMENTED", "2026-03-02T12:00:00Z")]), "approved")
        self.assertEqual(s(reviews=[("me", "DISMISSED", "2026-03-02T11:00:00Z")]), "review requested")

    def test_the_page_never_waits_for_github(self):
        with mock.patch.object(app, "_read_prs") as read, mock.patch.dict(app._prs, {"at": 0, "read": False}):
            out = app.prs()
            self.assertTrue(out["loading"])
            app.prs()
        self.assertLessEqual(read.call_count, 1)


class Page(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with mock.patch.object(app, "_read_prs"):
            cls.server, _ = app.serve(0)
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def call(self, path, body=None, headers=None):
        req = urllib.request.Request(self.url + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_the_page_and_its_scripts(self):
        for path in ("/", "/docket.js", "/page.js", "/docket.css", "/esc.js"):
            self.assertEqual(self.call(path)[0], 200, path)

    def test_another_host_name_is_refused(self):
        self.assertEqual(self.call("/state", headers={"Host": "evil.example:8766"})[0], 403)

    def test_a_change_needs_the_header(self):
        self.assertEqual(self.call("/task", {"action": "add", "value": "x"})[0], 403)
        code, body = self.call("/task", {"action": "add", "value": "Write the launch post", "kind": "deep"}, {"X-Docket": "1"})
        self.assertEqual(code, 200)
        self.assertIn("Write the launch post", [t["title"] for t in json.loads(body)["tasks"]])

    def test_a_bad_action_is_a_400(self):
        self.assertEqual(self.call("/task", {"id": "nope", "action": "done"}, {"X-Docket": "1"})[0], 400)


class Hooks(unittest.TestCase):
    """What an app that carries Docket inside it can change, without changing Docket."""

    def test_its_own_folder_and_settings_file(self):
        home, settings = store.HOME, store.SETTINGS
        try:
            with tempfile.TemporaryDirectory() as d:
                store.configure(home=d, settings=os.path.join(d, "app.json"))
                tasks.save({"tasks": [task()]})
                self.assertTrue(os.path.exists(os.path.join(d, "tasks.json")))
                store.write(store.SETTINGS, {"who": "Sam"})
                self.assertEqual(store.settings()["who"], "Sam")
        finally:
            store.HOME, store.SETTINGS = home, settings

    def test_fields_of_its_own_on_tasks_and_meetings(self):
        def share(item, current):
            if current is not None and "share" not in item:
                return {}
            return {"share": item.get("share") if item.get("share") in ("full", "none") else "none"}
        with mock.patch.object(tasks, "extra", share):
            t = task(share="full")
            self.assertEqual(t["share"], "full")
            reg = {"tasks": [t]}
            tasks.merge(reg, {"updates": [{"id": t["id"], "next": "x"}]}, DAY, NOW)
            self.assertEqual(t["share"], "full")  # an update that doesn't say keeps it
            tasks.merge(reg, {"updates": [{"id": t["id"], "share": "nonsense"}]}, DAY, NOW)
            self.assertEqual(t["share"], "none")
            self.assertEqual(tasks.meetings([{"start": "09:00", "end": "09:30", "title": "Sync", "share": "full"}])[0]["share"], "full")
        self.assertNotIn("share", task(share="full"))  # Docket alone keeps none

    def test_more_rules_and_a_callback_after_each_refresh(self):
        tasks.save({"tasks": []})
        seen = []
        with mock.patch.object(app, "_ask", return_value={"new": [{"title": "Reply to Alex"}]}) as ask, \
             mock.patch.object(app.sessions, "recent", return_value=[]), \
             mock.patch.object(app, "RULES", "Also mark what {who} may share."), \
             mock.patch.object(app, "AFTER_REFRESH", [seen.append]), \
             mock.patch.object(store, "settings", return_value={"who": "Sam"}):
            app.refresh()
        self.assertTrue(ask.call_args[0][0].endswith("Also mark what Sam may share."))
        self.assertEqual([t["title"] for t in seen[0]["tasks"]], ["Reply to Alex"])

    def test_its_own_page_and_routes(self):
        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as f:
            f.write("<p>mine</p>")
        try:
            with mock.patch.object(app, "_read_prs"):
                with mock.patch.object(app, "STATE", lambda: {"share": True}):
                    server, page = app.serve(0, index=f.name, routes={"/hub": lambda: {"url": "http://hub"}})
                    self.assertTrue(page.state()["share"])
            url = f"http://127.0.0.1:{server.server_port}"
            with urllib.request.urlopen(url + "/") as r:
                self.assertEqual(r.read(), b"<p>mine</p>")
            with urllib.request.urlopen(url + "/hub") as r:
                self.assertEqual(json.loads(r.read()), {"url": "http://hub"})
            server.shutdown()
        finally:
            os.unlink(f.name)
