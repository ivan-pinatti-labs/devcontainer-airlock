"""Allow and refuse cases for the gh broker, run when its image is built, so
an allowlist or parser change that reopens one fails the build."""
# cspell:words Rsomeone
import os
import sys
os.environ["GH_BROKER_OWNERS"] = "example-org"
src = open("/usr/local/libexec/gh-broker/broker.py").read()
ns = {"__name__": "broker"}; exec(compile(src, "broker.py", "exec"), ns)
allowed = ns["allowed"]
cases = [
  (True,  "pr list"), (True, "pr view 25 -R example-org/devcontainer-airlock"),
  (True,  "pr comment https://github.com/example-org/gh-actions/pull/1 --body hi"),
  (True,  "api repos/example-org/gh-actions"), (True, "api graphql -f query={viewer{login}}"),
  (False, "pr comment https://github.com/someone-else/repo/pull/1 --body spam"),
  (False, "pr view github.com/evil/x/pull/2"), (False, "pr list -R evil.com/example-org/x"),
  (False, "api -XPOST repos/example-org/gh-actions/issues"), (False, "api -X DELETE repos/example-org/x"),
  (False, "api graphql -F query=@m.graphql"), (False, "api graphql -Fquery=@m.graphql"),
  (False, "api graphql -f query=mutation{x}"), (False, "api --hostname evil.com repos/example-org/x"),
  (False, "api -H X-HTTP-Method-Override:DELETE repos/example-org/x"), (False, "api --input f repos/example-org/x"),
  (True,  "issue create -R example-org/x -t t --body-file -"),
  (True,  "pr create --title t --body-file - --draft"),
  (False, "issue create -R example-org/x -t t --body-file /proc/self/environ"),
  (False, "pr comment 25 --body-file=/proc/self/environ"),
  (False, "pr create -t t -F /home/other/.env"), (False, "pr create -t t -F/proc/self/environ"),
  (False, "issue create -t t --recover /proc/self/environ"),
  (True,  "api -X POST repos/example-org/x/pulls/25/comments/99/replies -f body=Fixed"),
  (True,  "api repos/example-org/x/pulls/25/comments/99/replies -f body=Declined"),
  (True,  "api graphql -f query=mutation{resolveReviewThread(input:{threadId:\"T_1\"}){thread{isResolved}}}"),
  (False, "api -X POST repos/someone-else/x/pulls/1/comments/2/replies -f body=spam"),
  (False, "api -X POST repos/example-org/x/pulls/25/comments/99/replies -F body=@/proc/self/environ"),
  (False, "api -X POST repos/example-org/x/pulls/25/comments/99/replies -f body=x -f in_reply_to=1"),
  (False, "api -X DELETE repos/example-org/x/pulls/25/comments/99/replies"),
  (False, "api repos/example-org/x/issues -f title=t"),
  (False, "api graphql -f query=mutation{deleteRepository(input:{repositoryId:\"R\"}){clientMutationId}}"),
  (False, "api graphql -f query=mutation{resolveReviewThread(input:{threadId:\"T\"}){thread{id}}x:deleteRepository(input:{repositoryId:\"R\"}){clientMutationId}}"),
  (False, "api graphql -f query=mutation{x:deleteRepository(input:{repositoryId:\"R\"}){clientMutationId}}"),
  (False, "api graphql -f query=mutation{...F}"),
  (True,  "pr merge 25 --auto -R example-org/x"), (True, "pr merge --auto --squash"),
  (False, "pr merge 25 --admin"), (False, "pr merge 25 --auto --admin=true"),
  (False, "pr merge https://github.com/evil/x/pull/1 --auto"),
  (False, "auth token"), (False, "repo delete example-org/x --yes"), (False, "secret list"), (False, "api user"),
  # Search is scoped by --owner as well as --repo, and by qualifiers in the
  # query; each is held to the same owners.
  (True,  "search issues flaky --owner example-org"), (True, "search prs --owner=EXAMPLE-ORG --state open"),
  (True,  "search issues --repo example-org/x,example-org/y deadlock"),
  (True,  "search issues repo:example-org/x is:open"), (True, "search prs org:example-org review"),
  (False, "search issues --owner someone-else"), (False, "search issues --owner=someone-else"),
  (False, "search prs --owner example-org,someone-else"), (False, "search prs --owner example-org-evil"),
  (False, "search issues --owner"), (False, "search issues --repo someone-else/x"),
  (False, "search issues --repo example-org/x,someone-else/y"), (False, "pr list -Rsomeone-else/x"), (False, "pr comment 1 -Rsomeone-else/x --body spam"),
  (False, "search issues repo:someone-else/x"), (False, "search issues is:open org:someone-else"),
  (False, "search prs user:someone-else"), (False, "search issues -repo:someone-else/x"),
]
# Cases whose arguments hold spaces or newlines, given as argv lists.
Q = "api graphql -f".split()
argv_cases = [
  (True,  Q + ["query=mutation($id:ID!){resolveReviewThread(input:{threadId:$id}){thread{isResolved}}}", "-f", "id=T_1"]),
  (True,  Q + ["query=mutation {\n  resolveReviewThread(input: {threadId: \"T_1\"}) {\n    thread { isResolved }\n  }\n}"]),
  # A comment the server skips hides a quote from the scan, and with it a
  # second operation (reported on PR 25).
  (False, Q + ["query=mutation{resolveReviewThread(input:{threadId:\"T\"}){thread{id}} #\"\ndeleteRepository(input:{repositoryId:\"R\"}){clientMutationId}}"]),
  (False, Q + ["query=mutation{resolveReviewThread(input:{threadId:\"\"\"T\"\"\"}){thread{id}}}"]),
  (False, Q + ["query=mutation{resolveReviewThread(input:{threadId:\"T\\\"\"}){thread{id}}}"]),
  (False, Q + ["query=mutation{resolveReviewThread(input:{threadId:\"T x\"}){thread{id}}}"]),
  (False, Q + ["query=mutation{﻿deleteRepository(input:{repositoryId:\"R\"}){clientMutationId}}"]),
  (False, Q + ["query=mutation"]),
  (True,  ["search", "issues", "repo:example-org/x is:open deadlock"]),
  (False, ["search", "issues", "deadlock (org:someone-else)"]),
  (True,  ["search", "issues", "deadlock (org:example-org)"]),
  (True,  ["search", "prs", "(repo:example-org/x OR repo:example-org/y) is:open"]),
  (False, ["search", "prs", "(repo:example-org/x OR repo:someone-else/y)"]),
  (False, ["search", "prs", "is:open repo:someone-else/x"]),
]
bad = [(exp, c) for exp, c in cases if allowed(c.split()) != exp]
# Owners come from the environment, and nothing is allowed without them.
for env, exp in [("", 0), ("evil name", 0), ("example-org", 1)]:
    os.environ["GH_BROKER_OWNERS"] = env
    fresh = {"__name__": "broker"}; exec(compile(src, "broker.py", "exec"), fresh)
    ok = len(fresh["OWNERS"]) == exp and fresh["allowed"]("pr view 1 -R example-org/x".split()) == bool(exp)
    if not ok:
        bad.append((bool(exp), f"GH_BROKER_OWNERS={env!r}"))
    cases.append((bool(exp), env))
bad += [(exp, " ".join(a)) for exp, a in argv_cases if allowed(a) != exp]
cases += argv_cases
# A refusal says why, so the caller can tell a file flag from a command
# that is not on the allowlist.
refusal = ns["refusal"]
why_cases = [
  ("pr create -t t --body-file /tmp/body.md", "stdin"),
  ("auth token", "not on the allowlist"),
  ("pr view 1 -R evil/x", "allowed owner"),
  ("search issues --owner evil", "allowed owner"),
  ("pr merge 25 --admin", "merge queue"),
  ("api -X DELETE repos/example-org/x", "gh api call"),
]
bad += [(False, f"{c} (refusal should name {w!r})") for c, w in why_cases if w not in (refusal(c.split()) or "")]
cases += [(False, c) for c, _ in why_cases]
for exp, c in bad: print("WRONG", "expected", "allow" if exp else "refuse", ":", c)
print(f"{len(cases)-len(bad)}/{len(cases)} cases as expected")
sys.exit(1 if bad else 0)
