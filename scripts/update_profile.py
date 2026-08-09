#!/usr/bin/env python3
import json, os, re, urllib.request, urllib.error
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
TOKEN = os.environ.get("GITHUB_TOKEN")
USERNAME = os.environ.get("GITHUB_REPOSITORY_OWNER") or os.environ.get("GITHUB_USERNAME")

if not TOKEN or not USERNAME:
    raise SystemExit("GITHUB_TOKEN and GITHUB_REPOSITORY_OWNER are required.")

def api(url, method="GET", payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {TOKEN}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())

def gql(query, variables=None):
    data = api("https://api.github.com/graphql", "POST",
               {"query": query, "variables": variables or {}})
    if data.get("errors"):
        raise RuntimeError(data["errors"])
    return data["data"]

# User stats.
user = api(f"https://api.github.com/users/{USERNAME}")
repos = []
page = 1
while page <= 10:
    chunk = api(f"https://api.github.com/users/{USERNAME}/repos?per_page=100&type=owner&sort=updated&page={page}")
    if not chunk:
        break
    repos.extend(chunk)
    if len(chunk) < 100:
        break
    page += 1

# Aggregate languages across public owned repositories.
lang_bytes = {}
for repo in repos:
    if repo.get("fork"):
        continue
    try:
        langs = api(repo["languages_url"])
        for name, amount in langs.items():
            lang_bytes[name] = lang_bytes.get(name, 0) + amount
    except Exception:
        pass

total = sum(lang_bytes.values()) or 1
langs = sorted(lang_bytes.items(), key=lambda x: x[1], reverse=True)[:4]
lang_values = [(name, round(value * 100 / total, 1)) for name, value in langs]

# Contribution calendar for the last year.
query = """
query($login:String!){
  user(login:$login){
    contributionsCollection {
      contributionCalendar {
        totalContributions
        weeks {
          contributionDays { date contributionCount }
        }
      }
    }
  }
}
"""
data = gql(query, {"login": USERNAME})
weeks = data["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]
days = [d for w in weeks for d in w["contributionDays"]]
counts = {d["date"]: d["contributionCount"] for d in days}

# Current / longest streak, treating a day with >0 contributions as active.
active = sorted(counts.keys())
longest = current = 0
run = 0
prev = None
for ds in active:
    d = date.fromisoformat(ds)
    if counts[ds] > 0:
        if prev and d == prev + timedelta(days=1):
            run += 1
        else:
            run = 1
        longest = max(longest, run)
        prev = d
    else:
        prev = None
        run = 0

today = date.today()
d = today
while d.isoformat() in counts and counts[d.isoformat()] > 0:
    current += 1
    d -= timedelta(days=1)

# Recent commit-ish count: repository push activity is not a contribution metric,
# so use total contributions for the visible metric and keep this label honest.
total_contributions = data["user"]["contributionsCollection"]["contributionCalendar"]["totalContributions"]

def replace_text(svg, element_id, value):
    pattern = rf'(<(?:text)[^>]*id="{re.escape(element_id)}"[^>]*>)(.*?)(</text>)'
    return re.sub(pattern, lambda m: m.group(1) + str(value) + m.group(3), svg, count=1, flags=re.S)

def replace_width(svg, element_id, width):
    pattern = rf'(<rect[^>]*id="{re.escape(element_id)}"[^>]*width=")[^"]+(")'
    return re.sub(pattern, lambda m: m.group(1) + str(width) + m.group(2), svg, count=1)

# Activity card.
p = ASSETS / "activity.svg"
s = p.read_text()
s = replace_text(s, "contributions", f"{total_contributions:,}")
s = replace_text(s, "current-streak", f"{current:02d}")
s = replace_text(s, "longest-streak", f"{longest:02d}")
s = replace_text(s, "repos", f"Repositories: {user.get('public_repos', 0)}")
s = replace_text(s, "followers", f"Followers: {user.get('followers', 0)}")
s = replace_text(s, "commits", "Contributions: live")
p.write_text(s)

# Language card.
p = ASSETS / "system-log.svg"
s = p.read_text()
for i in range(4):
    if i < len(lang_values):
        name, pct = lang_values[i]
    else:
        name, pct = "—", 0
    s = replace_text(s, f"lang{i+1}-name", name)
    s = replace_text(s, f"lang{i+1}-pct", f"{pct:.1f}%")
    s = replace_width(s, f"lang{i+1}-bar", max(0, min(520, round(520 * pct / 100))))
s = replace_text(s, "last-update", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
p.write_text(s)

# Stack card gets the current top language names.
p = ASSETS / "system-stack.svg"
s = p.read_text()
names = [x[0] for x in lang_values]
while len(names) < 3:
    names.append("—")
s = replace_text(s, "languages-1", " · ".join(names[:3]))
p.write_text(s)

print("Updated profile telemetry for", USERNAME)
