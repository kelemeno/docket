"""The task list's rules, your notes to Claude, the PR panel, and the local page's guards."""
import json
import os
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from unittest import mock

if "docket-test-" not in os.environ.get("DOCKET_HOME", ""):  # never your own list, however the tests are run
    os.environ["DOCKET_HOME"] = tempfile.mkdtemp(prefix="docket-test-")

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

    def test_long_text_is_cut_at_a_word(self):
        t = task(next="Make the fix: a disengaged agent re-engages only when pinged, even in its own thread " * 4)
        self.assertLessEqual(len(t["next"]), 240)
        self.assertTrue(t["next"].endswith("…"))
        self.assertTrue(t["next"][:-1].rsplit(" ", 1)[-1] in "Make the fix: a disengaged agent re-engages only when pinged, even in its own thread")
        long_link = "https://example.com/" + "a" * 500
        self.assertEqual(task(sources=[{"label": "x", "link": long_link}])["sources"][0]["link"], long_link[:400])

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


    def test_a_note_is_told_how_to_read_github(self):
        class Now:
            def __init__(self, target, daemon): self.target = target
            def start(self): self.target()
        with mock.patch.object(app, "_ask", return_value={"answer": "Found it."}) as ask, mock.patch.object(app, "_which", return_value="/bin/gh"), \
             mock.patch.object(app.threading, "Thread", Now):
            app.Page().reply(self.t["id"], "check whether Kalman has a PR for this")
        self.assertIn("one plain command at a time, no pipes", ask.call_args[0][0])


class Sources(unittest.TestCase):
    def test_one_link_per_slack_conversation_the_latest_under_the_first_label(self):
        dm = "https://x.enterprise.slack.com/archives/C1/p100"
        t = task(sources=[{"label": "group DM with Annie", "link": dm}])
        reg = {"tasks": [t]}
        tasks.merge(reg, {"updates": [{"id": t["id"], "sources": [
            {"label": "slack group DM", "link": "https://x-workspace.slack.com/archives/C1/p100"},
            {"label": "slack group DM again", "link": "https://x.enterprise.slack.com/archives/C1/p300"},
            {"label": "thread in #team", "link": "https://x.slack.com/archives/C2/p5?thread_ts=4.0"},
            {"label": "another reply", "link": "https://x.slack.com/archives/C2/p6?thread_ts=4.0"},
            {"label": "the PR", "link": "https://github.com/o/r/pull/1"}]}]}, DAY, NOW)
        self.assertEqual([(s["label"], s["link"].rsplit("/", 1)[-1]) for s in t["sources"]],
                         [("group DM with Annie", "p300"), ("thread in #team", "p6?thread_ts=4.0"), ("the PR", "1")])

    def test_the_same_message_under_another_host_is_a_duplicate(self):
        t = task(sources=[{"label": "dm", "link": "https://x.enterprise.slack.com/archives/C1/p100"}])
        reg = {"tasks": [t]}
        tasks.merge(reg, {"new": [{"title": "Something else", "sources": [{"label": "dm", "link": "https://x-workspace.slack.com/archives/C1/p100"}]}]}, DAY, NOW)
        self.assertEqual(len(reg["tasks"]), 1)


class Order(unittest.TestCase):
    def test_your_order_is_kept_without_unknown_ids_or_repeats(self):
        a, b = task(), task(title="Answer Sam")
        tasks.save({"tasks": [a, b]})
        reg = tasks.act(a["id"], "order", [b["id"], "nope", a["id"], b["id"]], DAY, NOW)
        self.assertEqual(reg["order"], [b["id"], a["id"]])
        self.assertEqual(tasks.load()["order"], [b["id"], a["id"]])
        with self.assertRaises(ValueError):
            tasks.act(a["id"], "order", "b", DAY, NOW)

    def test_dragging_a_plan_block_keeps_tasks_not_drawn_in_today(self):
        a, hidden, b, unsorted = (task(title=title) for title in ("A", "Waiting", "B", "Not in your order"))
        tasks.save({"tasks": [a, hidden, b, unsorted], "order": [a["id"], hidden["id"], b["id"]]})
        reg = tasks.act(b["id"], "order_move", {"ids": [b["id"]], "before": a["id"]}, DAY, NOW)
        self.assertEqual(reg["order"], [b["id"], a["id"], hidden["id"]])
        reg = tasks.act(unsorted["id"], "order_move", {"ids": [unsorted["id"]], "before": a["id"]}, DAY, NOW)
        self.assertEqual(reg["order"], [b["id"], unsorted["id"], a["id"], hidden["id"]])
        self.assertEqual(tasks.load()["order"], reg["order"])

    def test_dragging_before_a_task_to_sort_keeps_the_rest_of_your_order(self):
        a, hidden, unsorted = (task(title=title) for title in ("A", "Later", "To sort"))
        tasks.save({"tasks": [a, hidden, unsorted], "order": [a["id"], hidden["id"]]})
        reg = tasks.act(a["id"], "order_move", {"ids": [a["id"]], "before": unsorted["id"]}, DAY, NOW)
        self.assertEqual(reg["order"], [hidden["id"], a["id"]])

    def test_dragging_a_quick_batch_moves_all_its_tasks(self):
        a, hidden, quick1, quick2 = (task(title=title) for title in ("A", "Waiting", "Quick 1", "Quick 2"))
        tasks.save({"tasks": [a, hidden, quick1, quick2], "order": [a["id"], hidden["id"]]})
        reg = tasks.act(quick1["id"], "order_move", {"ids": [quick1["id"], quick2["id"]], "before": a["id"]}, DAY, NOW)
        self.assertEqual(reg["order"], [quick1["id"], quick2["id"], a["id"], hidden["id"]])


class Estimate(unittest.TestCase):
    def test_an_estimate_is_yours_and_only_one_of_the_offered(self):
        t = task()
        tasks.save({"tasks": [t]})
        t = tasks.act(t["id"], "est", "120", DAY, NOW)["tasks"][0]
        self.assertEqual((t["est"], "est" in t["mine"]), (120, True))
        with self.assertRaises(ValueError):
            tasks.act(t["id"], "est", "7", DAY, NOW)


class FromSession(unittest.TestCase):
    def test_a_task_made_from_a_session_claims_it(self):
        tasks.save({"tasks": []})
        reg = tasks.add("Prividium local setup", "deep", DAY, NOW, ["Prividium local setup"])
        self.assertEqual((reg["tasks"][0]["kind"], reg["tasks"][0]["sessions"]), ("deep", ["Prividium local setup"]))


class Kind(unittest.TestCase):
    def test_moving_a_task_to_work_is_yours_and_the_refresh_leaves_it(self):
        t = task()
        reg = {"tasks": [t]}
        tasks.save(reg)
        tasks.act(t["id"], "kind", "deep", DAY, NOW)
        reg = tasks.load()
        tasks.merge(reg, {"updates": [{"id": t["id"], "kind": "quick"}]}, DAY, NOW)
        self.assertEqual((reg["tasks"][0]["kind"], reg["tasks"][0]["mine"]), ("deep", ["kind"]))


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

    def test_gh_lookups_are_allowed_even_with_a_setup_from_before_them(self):
        allow = self.ran(False)["allow"]
        for t in ("Bash(gh search repos:*)", "Bash(gh repo view:*)", "Bash(gh pr list:*)"):
            self.assertEqual(allow.count(t), 1)

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

    def test_a_review_you_approved_closes_without_a_refresh(self):
        url = "https://github.com/Org/repo/pull/7"
        asked = task(title="Look at the CI pin", review=url + "/files")
        old = task(title="Review repo#7: pin CI", sources=[{"label": "github repo#7", "link": url + "#discussion_r1"}])
        two = task(title="Review repo#7 and repo#8", sources=[{"label": "a", "link": url}, {"label": "b", "link": url[:-1] + "8"}])
        other = task(title="Reply to Sam on repo#7", sources=[{"label": "github repo#7", "link": url}])
        yours = task(title="Review repo#7", review=url)
        linear = {**task(title="Review repo#7 on Linear", review=url), "linear": {"key": "EVM-1"}}  # linear.py's to close
        tasks.save({"tasks": [asked, old, two, other, yours, linear]})
        tasks.act(yours["id"], "waiting", None, DAY, NOW)  # you set its status: it stays yours
        node = {"number": 7, "title": "ci: pin", "url": url, "updatedAt": "2026-03-02T10:43:07Z", "repository": {"nameWithOwner": "Org/repo"},
                **self.reviewed(reviews=[("me", "APPROVED", "2026-03-02T10:43:07Z")], pushed="2026-03-02T10:11:20Z")}
        answer = mock.Mock(returncode=0, stdout=json.dumps({"data": {"viewer": {"login": "me"}, "search": {"nodes": [node]}}}))
        with mock.patch.object(app, "_which", return_value="gh"), mock.patch.object(app.subprocess, "run", return_value=answer), \
                mock.patch.dict(app._prs):
            app._read_prs()
            self.assertEqual(app._prs["reviewing"][0]["state"], "approved")
        got = {t["title"]: t for t in tasks.load()["tasks"]}
        self.assertEqual([got[t["title"]]["status"] for t in (asked, old, two, other, yours, linear)],
                         ["done", "done", "open", "open", "waiting", "open"])
        self.assertEqual(got[old["title"]]["closed_by"], "gh")
        self.assertTrue(got[old["title"]]["evidence"].startswith("You approved org/repo#7 on GitHub"))

    def test_the_refresh_names_the_pr_a_review_is_for(self):
        reg = {"tasks": []}
        tasks.merge(reg, {"new": [{"title": "Review repo#7", "review": "https://github.com/o/r/pull/7"},
                                  {"title": "Read the doc", "review": "https://example.com/doc"}]}, DAY, NOW)
        self.assertEqual([t.get("review") for t in reg["tasks"]], ["https://github.com/o/r/pull/7", None])
        tasks.merge(reg, {"updates": [{"id": reg["tasks"][1]["id"], "review": "https://github.com/o/r/pull/8"}]}, DAY, NOW)
        self.assertEqual(tasks.pr_key(reg["tasks"][1]["review"]), "o/r#8")

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

    def test_a_meeting_keeps_its_call_link_and_only_a_web_one(self):
        m = tasks.meetings([{"start": "09:00", "end": "09:30", "title": "Sync", "link": "https://meet.google.com/abc-defg-hij"},
                            {"start": "10:00", "end": "10:30", "title": "1:1", "link": "javascript:alert(1)"}])
        self.assertEqual([x.get("link") for x in m], ["https://meet.google.com/abc-defg-hij", None])

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
