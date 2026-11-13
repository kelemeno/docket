"""Linear on the list (linear.py): your issues and the reviews you're asked for in comments, whose
move each is from its PRs and your agent, and coupling the rest of the list to them."""
import json
import os
import tempfile
import unittest
from unittest import mock

if "docket-test-" not in os.environ.get("DOCKET_HOME", ""):  # never your own list, however the tests are run
    os.environ["DOCKET_HOME"] = tempfile.mkdtemp(prefix="docket-test-")

from docket import app, linear, store, tasks

DAY, NOW = "2026-03-02", "2026-03-02T12:00:00"
ME = {"id": "u1", "name": "Kalman Lajko", "displayName": "kl", "email": "kl@example.com"}
PR1, PR2 = "https://github.com/o/r/pull/1", "https://github.com/o/r/pull/2"


def issue(key, title="Do the thing", priority=0, sort=0.0, state="In Progress", kind="started", prs=(), over=()):
    return {"id": f"id-{key}", "identifier": key, "title": title, "url": f"https://linear.app/x/issue/{key}/slug",
            "priority": priority, "sortOrder": sort, "state": {"name": state, "type": kind},
            "attachments": {"nodes": [{"url": u, "metadata": {"status": "merged" if u in over else "open"}} for u in prs]}}


def comment(body, i, at="2026-03-01T10:00:00Z", by="Nino L"):
    return {"id": f"c-{at}", "body": body, "url": i["url"] + "#comment-1", "createdAt": at, "user": {"name": by}, "issue": i}


def data(assigned=(), comments=()):
    return {"viewer": {**ME, "assignedIssues": {"nodes": list(assigned)}}, "comments": {"nodes": list(comments)}}


def synced(feed, prs=None, existing=()):
    reg = {"tasks": list(existing)}
    linear.sync(reg, feed, prs or {}, DAY, NOW)
    return reg, {t["id"]: t for t in reg["tasks"]}


class Reviews(unittest.TestCase):
    def test_a_review_line_names_you_in_any_of_the_ways_a_mention_can(self):
        i = issue("EVM-1")
        for body in ("review: @kl", "review: @kalman high", "Review: [@kl](https://linear.app/x/profiles/kl) urgent",
                     "> review: https://linear.app/x/profiles/kl", "thanks!\nreview: @valera, @KalmanLajko low"):
            self.assertIn(i["id"], linear.reviews([comment(body, i)], ME), body)
        self.assertEqual(linear.reviews([comment("review: @valera high", i), comment("@kl see above", i)], ME), {})

    def test_a_mention_that_says_review_asks_too_as_linear_writes_it(self):
        i = issue("EVM-1")
        for body in ("In review by @kl", "@kl can you review this before the OZ handoff?", "@Kalman please review, high"):
            self.assertIn(i["id"], linear.reviews([comment(body, i)], ME), body)
        self.assertEqual(linear.reviews([comment("@kl review, high", i)], ME)[i["id"]][1], 2)
        for body in ("@kl wdyt?", "this is what being done in @kl branch", "In review by @valera"):
            self.assertEqual(linear.reviews([comment(body, i)], ME), {}, body)
        mine = {**comment("In review by @kl: " + PR1, i), "user": {"name": "Kalman Lajko", "displayName": "kl"}}
        self.assertIn(i["id"], linear.reviews([mine], ME), "you taking the review counts")

    def test_the_pr_an_ask_links_is_the_reviews_pr_and_a_duplicate_asks_nothing(self):
        i = issue("EVM-2")
        out = linear.items(data(comments=[comment(f"@kl review please: {PR1}", i)]))
        self.assertEqual(out[0]["prs"], [PR1])
        dup = issue("EVM-3", state="Duplicate", kind="duplicate")
        self.assertEqual(linear.items(data(comments=[comment("review: @kl", dup)])), [])

    def test_the_latest_line_counts_and_done_takes_it_off(self):
        i = issue("EVM-1")
        first, later = comment("review: @kl low", i, "2026-03-01T10:00:00Z"), comment("review: @kl high", i, "2026-03-01T11:00:00Z")
        self.assertEqual(linear.reviews([later, first], ME)[i["id"]][1], 2)
        self.assertEqual(linear.reviews([first, comment("review: @kl done", i, "2026-03-01T12:00:00Z")], ME), {})


class Items(unittest.TestCase):
    def test_assigned_and_reviews_in_linears_order_no_priority_last(self):
        out = linear.items(data([issue("A-1", priority=0), issue("A-2", priority=3, sort=2.0), issue("A-3", priority=3, sort=1.0)],
                                [comment("review: @kl urgent", issue("B-1", prs=[PR1]))]))
        self.assertEqual([(x["key"], x["role"], x["level"]) for x in out],
                         [("B-1", "reviewer", 1), ("A-3", "assignee", 3), ("A-2", "assignee", 3), ("A-1", "assignee", 5)])
        self.assertEqual((out[0]["prs"], out[0]["by"]), ([PR1], "Nino L"))

    def test_a_review_on_a_finished_issue_isnt_one(self):
        done = issue("B-1", state="Done", kind="completed")
        self.assertEqual(linear.items(data(comments=[comment("review: @kl", done)])), [])


def feed(*pairs):
    return linear.items(data([i for i, c in pairs if c is None], [c for i, c in pairs if c is not None]))


class Move(unittest.TestCase):
    def test_a_pr_that_needs_you_opens_it_and_one_waiting_on_others_makes_it_wait(self):
        i = issue("A-1", prs=[PR1])
        _, by = synced(feed((i, None)), {PR1: {"state": "CI failing"}})
        self.assertEqual((by["lin-A-1"]["status"], by["lin-A-1"]["next"]), ("open", "#1: CI failing"))
        _, by = synced(feed((i, None)), {PR1: {"state": "needs review"}})
        self.assertEqual((by["lin-A-1"]["status"], by["lin-A-1"]["next"]), ("waiting", "#1: needs review"))

    def test_a_review_is_done_when_its_prs_are_merged(self):
        i = issue("B-1", prs=[PR1], over=[PR1])
        reg, by = synced(feed((i, comment("review: @kl", i))))
        self.assertEqual(by, {})  # done before it was ever on the list: not added
        t = {"id": "lin-B-1", "status": "open", "sources": [], "mine": [], "linear": {"key": "B-1", "sources": []}}
        _, by = synced(feed((i, comment("review: @kl", i))), existing=[t])
        self.assertEqual((by["lin-B-1"]["status"], by["lin-B-1"]["closed_by"]), ("done", "linear"))

    def test_your_agents_task_for_the_pr_is_coupled_and_decides_the_move(self):
        agent = {"id": "aag-7", "title": "Fix CI", "next": "Say go", "status": "open", "kind": "quick", "aag": {"id": "7"},
                 "sources": [{"label": "o/r#1", "link": PR1}], "mine": []}
        reg, by = synced(feed((issue("A-1", prs=[PR1]), None)), {PR1: {"state": "needs review"}}, [agent])
        self.assertEqual(by["aag-7"]["issue"], "A-1")  # stays, shown with the issue
        reg2 = {"tasks": reg["tasks"]}
        linear.sync(reg2, feed((issue("A-1", prs=[PR1]), None)), {PR1: {"state": "needs review"}}, DAY, NOW)
        self.assertEqual(next(t for t in reg2["tasks"] if t["id"] == "lin-A-1")["status"], "open")  # your agent waits on you

    def test_priority_comes_from_linear_until_you_set_it(self):
        _, by = synced(feed((issue("A-1", priority=1), None)))
        self.assertEqual(by["lin-A-1"]["priority"], "P0")
        t = {**by["lin-A-1"], "priority": "P2", "mine": ["priority"]}
        _, by = synced(feed((issue("A-1", priority=1), None)), existing=[t])
        self.assertEqual(by["lin-A-1"]["priority"], "P2")


class Coupling(unittest.TestCase):
    def test_the_refreshs_task_about_an_issue_is_folded_in(self):
        slack = "https://x.slack.com/archives/C1/p1"
        n = tasks._new({"title": "Re-review Nino's PR #1", "why": "Nino asked in Slack",
                        "sources": [{"label": "o/r#1", "link": PR1}, {"label": "slack", "link": slack}]}, DAY, NOW)
        reg, by = synced(feed((issue("B-1", prs=[PR1]), comment("review: @kl", issue("B-1", prs=[PR1])))), {}, [n])
        self.assertEqual(list(by), ["lin-B-1"])
        self.assertEqual(by["lin-B-1"]["why"], "Nino asked in Slack")
        self.assertIn(slack, [s["link"] for s in by["lin-B-1"]["sources"]])

    def test_one_you_touched_is_folded_in_too_with_its_notes_but_not_while_claude_answers(self):
        n = {**tasks._new({"title": "Write the B-1 docs", "sources": [{"label": "o/r#1", "link": PR1}]}, DAY, NOW),
             "mine": ["kind"], "notes": [{"at": NOW, "by": "you", "text": "there is a linear ticket"}]}
        waiting = {**n, "id": "w1", "asked": NOW}
        reg, by = synced(feed((issue("B-1", prs=[PR1]), None)), {}, [n, waiting])
        self.assertEqual(sorted(by), ["lin-B-1", "w1"])  # w1 folds once Claude has answered its note
        self.assertEqual(by["lin-B-1"]["notes"][-1]["text"], "there is a linear ticket")

    def test_a_key_in_the_title_couples_and_yours_stay(self):
        mine = {**tasks._new({"title": "A-1: write the migration notes"}, DAY, NOW), "mine": ["title"]}
        _, by = synced(feed((issue("A-1"), None)), {}, [mine])
        self.assertEqual(by[mine["id"]]["issue"], "A-1")

    def test_an_issue_made_from_a_task_couples_by_its_title_or_a_pr_in_its_description_and_takes_its_place(self):
        agent = {"id": "aag-7", "title": "feat(v35): the upgrade engine", "next": "Your review", "status": "open", "kind": "deep",
                 "aag": {"id": "7"}, "sources": [{"label": "o/r#1", "link": PR1}], "mine": []}
        made = {**issue("A-9", title="feat(v35): the upgrade engine")}
        described = {**issue("A-8", title="Something else"), "description": f"Answer\n\n{PR2}"}
        other = {**tasks._new({"title": "Ping about it", "sources": [{"label": "pr", "link": PR2}]}, DAY, NOW), "mine": ["title"]}
        reg = {"tasks": [agent, other], "order": ["aag-7"]}
        linear.sync(reg, feed((made, None), (described, None)), {}, DAY, NOW)
        by = {t["id"]: t for t in reg["tasks"]}
        self.assertEqual((by["aag-7"]["issue"], by[other["id"]]["issue"]), ("A-9", "A-8"))
        self.assertEqual(reg["order"], ["lin-A-9"])

    def test_deep_work_on_no_issue_stays_uncoupled_and_an_issue_off_linear_is_handled(self):
        loose = tasks._new({"title": "Prepare calldata"}, DAY, NOW)
        reg, by = synced(feed((issue("A-1"), None)), {}, [loose])
        self.assertNotIn("issue", by[loose["id"]])
        linear.sync(reg, [], {}, DAY, NOW)
        lin = next(t for t in reg["tasks"] if t["id"] == "lin-A-1")
        self.assertEqual((lin["status"], lin["closed_by"]), ("done", "linear"))

    def test_your_done_stays_until_the_move_changes(self):
        i = issue("A-1", prs=[PR1])
        reg, _ = synced(feed((i, None)), {PR1: {"state": "CI failing"}})
        t = reg["tasks"][0]
        t.update(status="done", mine=["status"])
        linear.sync(reg, feed((i, None)), {PR1: {"state": "CI failing"}}, DAY, NOW)
        self.assertEqual(t["status"], "done")
        linear.sync(reg, feed((i, None)), {PR1: {"state": "conflicts"}}, DAY, NOW)
        self.assertEqual(t["status"], "open")


class Reading(unittest.TestCase):
    def test_the_query_has_the_key_and_errors_come_back_as_errors(self):
        class R:
            def __init__(self, body): self.body = body
            def read(self): return json.dumps(self.body).encode()
            def __enter__(self): return self
            def __exit__(self, *a): return False
        sent = {}

        def urlopen(req, timeout):
            sent.update(auth=req.get_header("Authorization"), body=json.loads(req.data))
            return R({"data": data([issue("A-1")])})
        with mock.patch("urllib.request.urlopen", urlopen):
            self.assertEqual(linear.read("lin_api_x", 7)["viewer"]["displayName"], "kl")
        self.assertEqual((sent["auth"], sent["body"]["variables"]), ("lin_api_x", {"since": "-P7D"}))
        with mock.patch("urllib.request.urlopen", lambda req, timeout: R({"errors": [{"message": "bad key"}]})):
            with self.assertRaisesRegex(RuntimeError, "bad key"):
                linear.read("x")

    def test_poll_without_a_key_reads_nothing_and_says_so(self):
        with mock.patch.object(linear, "key", return_value=""), mock.patch.object(tasks, "load", return_value={"tasks": []}):
            linear.poll()
        self.assertEqual(linear.state()["linear"], {"key": False, "at": None, "error": None, "since": None, "count": 0, "team": None, "theirs": {}})

    def test_your_team_is_the_settings_one_else_your_first_live_one_on_linear(self):
        d = data()
        d["viewer"]["teams"] = {"nodes": [{"key": "DOC", "archivedAt": "2025-01-01"}, {"key": "EVM", "archivedAt": None}]}
        self.assertEqual(linear.team(d), "EVM")
        self.assertIsNone(linear.team(data()))
        with mock.patch.object(store, "settings", return_value={"linear_team": "OPS"}):
            self.assertEqual(linear.state()["linear"]["team"], "OPS")

    def test_the_rules_go_through_rules_from_and_leave_rules_alone(self):
        app.RULES = "HOST RULES"
        try:
            reg, _ = synced(feed((issue("A-1"), None)))
            text = linear.rules(reg)
            self.assertIn("lin-A-1 (A-1", text)
            self.assertEqual(app.RULES, "HOST RULES")
            text.format(who="Sam")  # braces are escaped for the refresh's format
        finally:
            app.RULES = ""


if __name__ == "__main__":
    unittest.main()


class Theirs(unittest.TestCase):
    """A task on nothing of yours on Linear: the issue that's already there, or the PR author's."""
    USERS = [{"id": "u-nino", "name": "Nino Loria", "displayName": "nino", "email": "nino@x.io", "gitHubUserId": None,
              "teams": {"nodes": [{"key": "OLD", "archivedAt": "2025"}, {"key": "EVM", "archivedAt": None}]}},
             {"id": "u-vg", "name": "Valera G", "displayName": "vg", "email": "v@x.io", "gitHubUserId": "777", "teams": {"nodes": []}}]

    def test_wanted_is_open_tasks_on_nothing_with_a_key_or_a_pr(self):
        reg = {"tasks": [
            {"id": "a", "title": "Review Nino's PR", "status": "open", "sources": [{"label": "pr", "link": PR1}]},
            {"id": "b", "title": "EVM-9 follow-up", "status": "open", "sources": []},
            {"id": "c", "title": "on it", "status": "open", "issue": "EVM-1", "sources": [{"label": "pr", "link": PR2}]},
            {"id": "d", "title": "agent", "status": "open", "aag": {"id": "x"}, "sources": [{"label": "pr", "link": PR2}]},
            {"id": "e", "title": "done", "status": "done", "sources": [{"label": "pr", "link": PR2}]},
            {"id": "f", "title": "nothing", "status": "open", "sources": []}]}
        self.assertEqual(linear.wanted(reg), {"a": ([], [PR1]), "b": (["EVM-9"], [])})

    def test_the_author_is_found_by_github_account_then_name_then_login(self):
        self.assertEqual(linear.author({"author": "vgh", "author_id": 777}, self.USERS)["id"], "u-vg")
        self.assertEqual(linear.author({"author": "nl", "author_name": "Nino Loria"}, self.USERS),
                         {"id": "u-nino", "name": "Nino Loria", "team": "EVM"})
        self.assertEqual(linear.author({"author": "NINO"}, self.USERS)["id"], "u-nino")
        self.assertIsNone(linear.author({"author": "someone", "author_name": "Some One"}, self.USERS))
        # no name on GitHub: the login against the name, or a first name only one person has
        self.assertEqual(linear.author({"author": "nino-loria"}, self.USERS)["id"], "u-nino")
        self.assertEqual(linear.author({"author": "0xValera"}, self.USERS)["id"], "u-vg")
        two = self.USERS + [{"id": "u-v2", "name": "Valera Other", "teams": {"nodes": []}}]
        self.assertIsNone(linear.author({"author": "0xValera"}, two))

    def test_lookup_finds_an_open_issue_first_and_a_reviewed_prs_author(self):
        done = {"id": "i1", "identifier": "EVM-1", "title": "Old", "url": "u1", "state": {"name": "Done", "type": "completed"},
                "assignee": {"name": "Valera G", "displayName": "vg"}, "team": {"key": "EVM"}}
        live = {**done, "id": "i2", "identifier": "EVM-2", "state": {"name": "In Progress", "type": "started"}}
        sent = {}

        def post(api_key, query, variables=None, partial=False):
            sent["q"] = query
            return {"k0": {"nodes": []}, "p0": {"nodes": [{"issue": done}, {"issue": live}]}, "p1": {"nodes": []}, "users": {"nodes": self.USERS}}
        want = {"a": (["NOPE-1"], [PR1]), "b": ([], [PR2])}
        reviewing = {PR2: {"url": PR2, "title": "feat: x", "author": "nl", "author_name": "Nino Loria"}}
        with mock.patch.object(linear, "_post", post):
            out = linear.lookup("k", want, reviewing)
        self.assertIn("users(", sent["q"])
        self.assertEqual((out["a"]["issue"]["key"], out["a"]["issue"]["who"], out["a"]["author"]), ("EVM-2", "vg", None))
        self.assertEqual((out["b"]["issue"], out["b"]["author"]["id"], out["b"]["pr"]["title"]), (None, "u-nino", "feat: x"))

    def test_marking_your_review_comments_on_the_open_issue_and_reads_again(self):
        calls = []
        with mock.patch.dict(linear._feed, theirs={"a": {"issue": {"id": "i2", "key": "EVM-2", "over": False}},
                                                   "b": {"issue": {"id": "i1", "key": "EVM-1", "over": True}}}, me=ME), \
                mock.patch.object(linear, "key", return_value="k"), mock.patch.object(linear, "poll") as poll, \
                mock.patch.object(linear, "_post", lambda k, q, v=None, partial=False: calls.append(v) or {"commentCreate": {"success": True}}):
            linear.mark_review({"id": "a"})
            self.assertEqual(calls, [{"i": "i2", "b": "In review by @kl"}])
            poll.assert_called_once_with(read_linear=True)
            with self.assertRaises(ValueError):
                linear.mark_review({"id": "b"})
        # and the comment makes it your review
        i = issue("EVM-2")
        self.assertEqual([x["role"] for x in linear.items(data(comments=[comment("In review by @kl", i, by="Kalman Lajko")]))], ["reviewer"])
