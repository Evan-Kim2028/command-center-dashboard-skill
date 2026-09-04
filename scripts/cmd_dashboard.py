#!/usr/bin/env python3
"""Command Code dashboard: everything on disk about cmd, taste, sessions, cost, and how taste steers the agent.
Usage: python3 cmd_dashboard.py [--public] [--project DIR] [--out FILE] [--no-open]
Self-contained HTML, no external deps. Reads ~/.commandcode and <project>/.commandcode."""
import re, os, sys, json, glob, html, math, bisect, collections, datetime, argparse, subprocess

ap = argparse.ArgumentParser()
ap.add_argument("--public", action="store_true", help="redact names/hosts/paths for sharing")
ap.add_argument("--project", default=os.getcwd(), help="project dir containing .commandcode/taste")
ap.add_argument("--out", default=None)
ap.add_argument("--no-open", action="store_true")
ap.add_argument("--redact", default=None, help="JSON file with extra [[pattern, replacement], ...]")
ap.add_argument("--list", action="store_true", help="list Command Code project dirs found on this machine and exit")
A = ap.parse_args()
HOME = os.path.expanduser("~")
PROJ = os.path.abspath(A.project)
CC_HOME = f"{HOME}/.commandcode"
TASTE = f"{PROJ}/.commandcode/taste/taste.md"
if not os.path.exists(TASTE):
    TASTE = f"{CC_HOME}/taste/taste.md"
slug = re.sub(r"[^a-z0-9]+", "-", PROJ.lower()).strip("-")
SESS = f"{CC_HOME}/projects/{slug}"
def _proj_dirs():
    out = []
    for d in glob.glob(f"{CC_HOME}/projects/*"):
        n = len([f for f in glob.glob(f"{d}/*.jsonl") if not f.endswith("checkpoints.jsonl")])
        if n: out.append((n, d))
    return sorted(out, reverse=True)
if A.list:
    for n, d in _proj_dirs(): print(f"{n:4d} sessions  {d}")
    sys.exit(0)
if not os.path.isdir(SESS) or not glob.glob(f"{SESS}/*.jsonl"):
    pd = _proj_dirs()
    if pd:
        SESS = pd[0][1]; print(f"note: no cmd sessions for {PROJ}; using {SESS} ({pd[0][0]} sessions). Pass --project to choose.", file=sys.stderr)
    else:
        print(f"error: no Command Code sessions found under {CC_HOME}/projects. Run a few cmd sessions first.", file=sys.stderr); sys.exit(2)
CFG = f"{SESS}/config.json"
OUT = A.out or f"{PROJ}/cmd-dashboard{'-public' if A.public else ''}.html"
PUBLIC = A.public

# ---------------- redaction ----------------
REDACT = []
for cand in [A.redact, f"{PROJ}/.commandcode/redact.json", f"{CC_HOME}/redact.json"]:
    if cand and os.path.exists(cand):
        REDACT += [tuple(x) for x in json.load(open(cand))]
REDACT = [(re.escape(HOME), "~")] + REDACT + [(r"\b" + re.escape(os.path.basename(HOME)) + r"\b", "the user")]
def redact(t):
    if not PUBLIC: return t
    for pat, rep in REDACT: t = re.sub(pat, rep, t, flags=re.I)
    return t
def esc(s): return html.escape(str(s))

# ---------------- taste bullets ----------------
def _session_cwd():
    for f in glob.glob(f"{SESS}/*.jsonl"):
        try:
            o = json.loads(open(f, errors="ignore").readline())
            if o.get("cwd"): return o["cwd"]
        except Exception: pass
    return None
def _taste_for(sess_dir):
    for f in glob.glob(f"{sess_dir}/*.jsonl"):
        try:
            o = json.loads(open(f, errors="ignore").readline()); c = o.get("cwd")
            if c and os.path.exists(f"{c}/.commandcode/taste/taste.md") and os.path.getsize(f"{c}/.commandcode/taste/taste.md") > 0: return f"{c}/.commandcode/taste/taste.md"
        except Exception: pass
    return None
if not (os.path.exists(TASTE) and os.path.getsize(TASTE) > 0):
    t = _taste_for(SESS)
    if not t:
        for n, d in _proj_dirs():
            t = _taste_for(d)
            if t: SESS = d; print(f"note: switching to {d} ({n} sessions) because it has a taste file", file=sys.stderr); break
    if not t and os.path.exists(f"{CC_HOME}/taste/taste.md") and os.path.getsize(f"{CC_HOME}/taste/taste.md") > 0: t = f"{CC_HOME}/taste/taste.md"
    if t: TASTE = t; print(f"note: using taste file {TASTE}", file=sys.stderr)
raw = open(TASTE).read() if os.path.exists(TASTE) else ""
if not raw.strip():
    print(f"error: no taste bullets found (looked at {TASTE}). Run /taste or cmd learn-taste first.", file=sys.stderr); sys.exit(2)
bul = [l[2:].strip() for l in raw.splitlines() if l.startswith("- ")]
seen = set(); bullets = []
for b in bul:
    if b[:80] in seen: continue
    seen.add(b[:80]); bullets.append(b)
def conf(b):
    m = re.search(r"Confidence:\s*([0-9.]+)", b); return float(m.group(1)) if m else 0.7
def strip(b): return redact(re.sub(r"\s*Confidence:.*$", "", b))

TRAITS = [
    ("Evidence-obsessed", "wants claims verified against primary artifacts, re-checks its own findings",
     r"evidence|verify|verif|primary|re-verify|hostile|adversarial|exaggerat|calibrated|hypothes|reproduc|actual code|inspection|cross-referenc|prove"),
    ("Process-gated", "plan, align, stage, explicit go-ahead before prod or issues",
     r"go-ahead|gates?|align|approv|staged|read-only|dry-run|before (any|enabling|proposing|shipping|recommending|terminating|announce)|Update before"),
    ("Complexity-averse", "fewer layers, one owner per service, maintainability over structure",
     r"complexity|simplif|maintainab|one clear owner|collapsing|too many|reason about|mirror .* conventions|rather than inventing|not merely"),
    ("Ops-hardened", "systemd, VPS, memory, exit codes, locks, hard-won gotchas",
     r"systemd|gotcha|VPS|SSH|host|journal|timer|admission|memory|swap|exit|lock|EnvironmentFile|NODE_ENV"),
    ("Parallel & cheap", "subagent fan-out, free models, backfill now",
     r"subagent|parallel|dynamic workflow|Haiku|free|cost|token|cache|backfill|spend"),
    ("Durable-state", "everything in GitHub, ADRs, skills; resumable from any machine",
     r"GitHub|ADR|persist|repo docs|skills?|resum|any machine|multiple machines|umbrella|tracker|Blocked by|documented"),
    ("Terse in, structured out", "one-line reports expected to be self-diagnosed; verdict-first output",
     r"verdict-first|brief|terse|one-liner|status update|summary|columns|table|framing|reading level|picturebook|high-level|delta|histogram|short multi-post"),
    ("Data-quality guardian", "write-time fences, goldens, canaries, provenance",
     r"guard|invariant|canary|golden|fence|integrity|provenance|quality|eval|degenerate|known_bad|detector"),
    ("Open-by-default product", "free public API/MCP, rate limits not paywalls, docs before announce",
     r"free|public|paywall|rate limit|MCP|announce|changelog|llms\.txt|OpenAPI|external agents|frictionless"),
]
def traits(b): return [t for t, _, p in TRAITS if re.search(p, b, re.I)]
DOMAINS = [
    ("production data lake", r"lake|gold\.|Iceberg|builder|sidecar|partition|pipeline|canary|golden|eval|retention|systemd|VPS|admission|scrap|oracle"),
    ("public API / MCP", r"MCP|public|API|announce|changelog|llms|OpenAPI|chat|router"),
    ("engineering process", r"GitHub|issue|CI|worktree|merge|commit|PR\b|ADR|plan|workflow|subagent|skill|verify|report|status"),
    ("job search", r"job|resume|application|ATS|relocat"),
    ("media", r"voice|reel|video|render|episode"),
]
def domain(b):
    for d, p in DOMAINS:
        if re.search(p, b, re.I): return d
    return "other"
rows = [dict(i=i + 1, raw=b, text=strip(b), conf=conf(b), traits=traits(b), domain=domain(b), n=len(b)) for i, b in enumerate(bullets)]
# bullet rare-token index for activation matching
WORD = re.compile(r"[a-z][a-z0-9_\-]{3,}")
STOPB = set("that with this from when than rather into over each also only just then them they their there these those which while where what want wants prefer prefers expect expects should would could before after about against between across both been being have have had not are was were its own via per one two all any some such more most less same other every never always user users agent agents confidence work working code file files data e.g. etc than when the and for".split())
btoks = [set(w for w in WORD.findall(r["raw"].lower()) if w not in STOPB) for r in rows]
bdf = collections.Counter(w for s in btoks for w in s)
brare = [{w for w in s if bdf[w] <= max(2, 0.08 * len(rows))} for s in btoks]

# ---------------- sessions ----------------
cite_pat = re.compile(r"taste", re.I)
steer_pat = re.compile(r"taste[^.\n]{0,120}\b(so|therefore|should|must|need to|don't|do not|instead|avoid|skip|never|not |before|gate|first)\b", re.I)
push_pat = re.compile(r"\b(unacceptable|add (that|it) back|wrong|revert|undo|that'?s not|you can'?t just|not what i|why did you|i said|stop doing)\b", re.I)
sessions = []; acts = []; learn_events = []; steer_quotes = []; tps_samples = []; skill_events = []
for f in sorted(glob.glob(f"{SESS}/*.jsonl")):
    if f.endswith("checkpoints.jsonl"): continue
    sid = os.path.basename(f)[:8]
    meta = {}
    mf = f.replace(".jsonl", ".meta.json")
    if os.path.exists(mf): meta = json.load(open(mf))
    S = dict(sid=sid, title=redact(meta.get("title") or ""), asst=0, user=0, cites=0, steer=0, inp=0, out=0, cr=0, cw=0, cost=0.0,
             models=collections.Counter(), tools=collections.Counter(), first=None, last=None, first_in=None, prompts=[], push=0,
             push_after_cite=0, push_after_nocite=0, turns_cite=0, turns_nocite=0, think_chars=0, bullets_hit=collections.Counter(), pm=collections.defaultdict(collections.Counter), skills=collections.Counter(), turns=[])
    last_turn_cited = False
    tool_names = {}; prev_time = None
    def _ts(t):
        try: return datetime.datetime.fromisoformat(t.rstrip("Z"))
        except Exception: return None
    for line in open(f, errors="ignore"):
        try: o = json.loads(line)
        except Exception: continue
        if o.get("type") != "message": continue
        m = o.get("message", {}); ts = o.get("timestamp") or ""
        S["first"] = S["first"] or ts; S["last"] = ts
        _ca = (m.get("meta") or {}).get("createdAt")
        rec_time = datetime.datetime.fromtimestamp(_ca / 1000, datetime.timezone.utc).replace(tzinfo=None) if (_ca and m.get("role") == "user") else _ts(ts)

        if m.get("role") == "assistant":
            mdl = o.get("model") or meta.get("model") or "?"
            S["asst"] += 1; S["models"][mdl] += 1
            u = o.get("usage") or {}
            ca = (m.get("meta") or {}).get("createdAt")
            if ca and prev_time and u.get("outputTokens", 0) > 0:
                dur = (datetime.datetime.fromtimestamp(ca / 1000, datetime.timezone.utc).replace(tzinfo=None) - prev_time).total_seconds()
                if 0.3 < dur < 1800:
                    PMx = S["pm"][mdl]; PMx["secs"] += dur; PMx["out_timed"] += u["outputTokens"]; PMx["timed"] += 1
                    tps_samples.append((mdl, u["outputTokens"] / dur))
            PM = S["pm"][mdl]; PM["asst"] += 1; PM["inp"] += u.get("inputTokens", 0); PM["out"] += u.get("outputTokens", 0); PM["cr"] += u.get("cacheReadTokens", 0); PM["cost"] += u.get("costUsd", 0) or 0
            TR_ = dict(ts=ts, model=mdl, inp=u.get("inputTokens", 0), out=u.get("outputTokens", 0), cr=u.get("cacheReadTokens", 0), cw=u.get("cacheWriteTokens", 0), cost=u.get("costUsd", 0) or 0, think=0, cited=False, steer=0, tools=[], skills=[]); S["turns"].append(TR_)
            S["inp"] += u.get("inputTokens", 0); S["out"] += u.get("outputTokens", 0)
            S["cr"] += u.get("cacheReadTokens", 0); S["cw"] += u.get("cacheWriteTokens", 0); S["cost"] += u.get("costUsd", 0) or 0
            if S["first_in"] is None and u.get("inputTokens"): S["first_in"] = u["inputTokens"]
            cited_here = False
            for c in m.get("content", []):
                if c.get("type") == "tool_use":
                    S["tools"][c.get("name")] += 1; tool_names[c.get("id")] = c.get("name"); TR_["tools"].append(c.get("name"))
                    if c.get("name") == "activate_skill":
                        skn = (c.get("input") or {}).get("name") or "(unnamed)"; S["skills"][skn] += 1; TR_["skills"].append(skn); skill_events.append(dict(sid=sid, date=ts[:10], ts=ts, skill=skn, turn=S["asst"]))
                if c.get("type") == "thinking":
                    th = c.get("thinking", ""); S["think_chars"] += len(th); PM["think"] += len(th); TR_["think"] += len(th)
                    if cite_pat.search(th):
                        S["cites"] += 1; cited_here = True; PM["cites"] += 1; TR_["cited"] = True
                        steer = bool(steer_pat.search(th)); S["steer"] += steer; PM["steer"] += steer; TR_["steer"] += steer
                        # attribute to bullets: look at windows around 'taste'
                        for mm in re.finditer(r"taste", th, re.I):
                            win = th[max(0, mm.start() - 300): mm.end() + 300].lower()
                            wt = set(WORD.findall(win))
                            best = max(range(len(rows)), key=lambda k: len(brare[k] & wt))
                            sc = len(brare[best] & wt)
                            if sc >= 2:
                                acts.append(dict(sid=sid, date=ts[:10], ts=ts, b=best, steer=steer, turn=S["asst"], model=mdl))
                                S["bullets_hit"][best] += 1
                                if steer and len(steer_quotes) < 400:
                                    q = th[max(0, mm.start() - 160): mm.end() + 220].replace("\n", " ")
                                    steer_quotes.append((ts[:10], sid, best, redact(q)))
            if cited_here: S["turns_cite"] += 1
            else: S["turns_nocite"] += 1
            last_turn_cited = cited_here
            prev_time = rec_time
        elif m.get("role") == "user":
            for c in m.get("content", []):
                if c.get("type") == "text":
                    t = c.get("text", ""); S["user"] += 1
                    is_push = bool(not t.lstrip().startswith("Error:") and push_pat.search(t[:200]))
                    S["prompts"].append((ts, redact(t), is_push, last_turn_cited))
                    if is_push:
                        S["push"] += 1
                        if last_turn_cited: S["push_after_cite"] += 1
                        else: S["push_after_nocite"] += 1
                if c.get("type") == "tool_result" and "learning your preference in the background" in json.dumps(c.get("content")):
                    learn_events.append((ts[:10], sid))
            prev_time = rec_time
    if S["asst"] == 0: continue
    S["date"] = (S["first"] or "")[:10]
    S["model"] = S["models"].most_common(1)[0][0] if S["models"] else "?"
    try:
        S["minutes"] = round((datetime.datetime.fromisoformat(S["last"].rstrip("Z")) - datetime.datetime.fromisoformat(S["first"].rstrip("Z"))).total_seconds() / 60)
    except Exception: S["minutes"] = 0
    sessions.append(S)
sessions.sort(key=lambda s: s["date"])

def clip_session(S, cut):
    """Copy of a session with every aggregate recomputed from turns/prompts at or after ISO timestamp `cut`."""
    T = [t for t in S["turns"] if t["ts"] >= cut]; P = [p for p in S["prompts"] if p[0] >= cut]
    if not T: return None
    C = dict(S); C["turns"] = T; C["prompts"] = P
    C["asst"] = len(T); C["user"] = len(P)
    for k in ("inp", "out", "cr", "cw", "cost"): C[k] = sum(t[k] for t in T)
    C["think_chars"] = sum(t["think"] for t in T); C["cites"] = sum(1 for t in T if t["cited"]); C["steer"] = sum(t["steer"] for t in T)
    C["turns_cite"] = C["cites"]; C["turns_nocite"] = len(T) - C["cites"]
    C["tools"] = collections.Counter(n for t in T for n in t["tools"]); C["skills"] = collections.Counter(n for t in T for n in t["skills"])
    C["models"] = collections.Counter(t["model"] for t in T); C["model"] = C["models"].most_common(1)[0][0]
    pm = collections.defaultdict(collections.Counter)
    for t in T:
        Q = pm[t["model"]]; Q["asst"] += 1; Q["inp"] += t["inp"]; Q["out"] += t["out"]; Q["cr"] += t["cr"]; Q["cost"] += t["cost"]; Q["think"] += t["think"]; Q["cites"] += t["cited"]; Q["steer"] += t["steer"]
    C["pm"] = pm
    C["push"] = sum(1 for p in P if p[2]); C["push_after_cite"] = sum(1 for p in P if p[2] and p[3]); C["push_after_nocite"] = sum(1 for p in P if p[2] and not p[3])
    ts_all = [t["ts"] for t in T] + [p[0] for p in P]; C["first"] = min(ts_all); C["last"] = max(ts_all); C["date"] = C["first"][:10]
    try: C["minutes"] = round((datetime.datetime.fromisoformat(C["last"].rstrip("Z")) - datetime.datetime.fromisoformat(C["first"].rstrip("Z"))).total_seconds() / 60)
    except Exception: C["minutes"] = 0
    return C
cfg = json.load(open(CFG))["tasteOnboarding"] if os.path.exists(CFG) else {}
learned = {k: len(v) for k, v in cfg.get("learnedSessions", {}).items()}
skipped = {k: len(v) for k, v in cfg.get("skippedSessions", {}).items()}
hist = []
if os.path.exists(f"{CC_HOME}/history.jsonl"):
    for l in open(f"{CC_HOME}/history.jsonl", errors="ignore"):
        try: hist.append(json.loads(l))
        except Exception: pass
n_filehist = len(glob.glob(f"{CC_HOME}/file-history/*/*"))

# ---------------- bullet dates (match to learned-from sessions, else interpolate) ----------------
cand = []
for i in cfg.get("learnedSessions", {}).get("claude-code", []):
    cand += glob.glob(f"{HOME}/.claude/projects/*/{i}.jsonl")
for i in cfg.get("learnedSessions", {}).get("cursor", []):
    cand += glob.glob(f"{HOME}/.cursor/projects/*/agent-transcripts/{i}/{i}.jsonl")
_MON = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}
cand += [f for f in glob.glob(f"{SESS}/*.jsonl") if not f.endswith("checkpoints.jsonl")]
docs = {}
for f in cand:
    try: txt = open(f, errors="ignore").read().lower()
    except Exception: continue
    m = re.search(r'"timestamp":"(\d{4}-\d{2}-\d{2})', txt)
    if m: d_ = m.group(1)
    else:
        mc = re.search(r"<timestamp>\w+, (\w{3}) (\d{1,2}), (\d{4})", txt, re.I)  # Cursor transcripts stamp turns in prose
        d_ = f"{mc.group(3)}-{_MON.get(mc.group(1).title(), 1):02d}-{int(mc.group(2)):02d}" if mc and mc.group(1).title() in _MON else datetime.date.fromtimestamp(os.path.getmtime(f)).isoformat()
    docs[f] = (d_, set(WORD.findall(txt)))
df = collections.Counter(w for _, (_, ws) in docs.items() for w in ws); N = max(1, len(docs))
for k, r in enumerate(rows):
    rare = {t for t in btoks[k] if df.get(t) and df[t] <= max(3, 0.3 * N)}
    best = None
    for f, (date, ws) in docs.items():
        sc = len(rare & ws)
        if sc and (best is None or sc > best[0] or (sc == best[0] and date < best[1])): best = (sc, date, f)
    ok = best and best[0] >= 3 and best[0] >= 0.25 * max(1, len(rare))
    r["date"] = best[1] if ok else None
    r["interp"] = r["date"] is None
    if ok:
        bf = best[2]
        if "/.claude/" in bf: r["src"] = "Claude Code"
        elif "/.cursor/" in bf: r["src"] = "Cursor"
        else:
            mf = bf.replace(".jsonl", ".meta.json"); mm = json.load(open(mf)).get("model") if os.path.exists(mf) else None
            if not mm:
                mm_ = re.search(r'"model":"([^"]+)"', open(bf, errors="ignore").read()); mm = mm_.group(1) if mm_ else "?"
            r["src"] = f"cmd · {mm}"
    else: r["src"] = "unmatched"
idx_d = [k for k, r in enumerate(rows) if r["date"]]
for k, r in enumerate(rows):
    if r["date"] or not idx_d: continue
    p = bisect.bisect_left(idx_d, k); lo = idx_d[p - 1] if p else None; hi = idx_d[p] if p < len(idx_d) else None
    D = lambda j: datetime.date.fromisoformat(rows[j]["date"])
    if lo is not None and hi is not None: d = D(lo) + datetime.timedelta(days=round((D(hi) - D(lo)).days * (k - lo) / (hi - lo)))
    else: d = D(lo if lo is not None else hi)
    r["date"] = d.isoformat()
for r in rows: r["date"] = r["date"] or datetime.date.today().isoformat()

# ---------------- svg helpers (all attributes quoted) ----------------
def svg_open(w, h, extra=""): return f"<svg class='chart' viewBox='0 0 {w} {h}' width='100%' preserveAspectRatio='xMidYMin meet' style='max-width:{w}px;{extra}'>"
def T(x, y, s, size=11, weight="normal", anchor="start", fill="var(--fg)", rot=None):
    tr = f" transform='rotate(-90 {x} {y})'" if rot else ""
    return f"<text x='{x:.0f}' y='{y:.0f}' text-anchor='{anchor}' fill='{fill}' style='font-size:{size}px;font-weight:{weight}'{tr}>{esc(s)}</text>"
def L(x1, y1, x2, y2, c="var(--axis)", w=1.5): return f"<line x1='{x1:.0f}' y1='{y1:.0f}' x2='{x2:.0f}' y2='{y2:.0f}' stroke='{c}' stroke-width='{w}'/>"
def R(x, y, w, h, c, title="", op=.9, cls=""): return f"<rect class='{cls}' x='{x:.0f}' y='{y:.0f}' width='{max(0,w):.0f}' height='{h:.0f}' fill='{c}' opacity='{op}' rx='2'><title>{esc(title)}</title></rect>"
def title_block(w, t, sub=None):
    s = T(22, 26, t, 16 if len(t) * 9 < w - 260 else 13, "bold")
    if sub: s += T(22, 46, sub, 12, fill="var(--muted)")
    return s

def hbars(title, items, xlabel, sub=None, w=900, color="var(--bar)", ylabel=None, lw=250, sort=True):
    """items: [(label, value, hover)]"""
    if sort: items = sorted(items, key=lambda x: -x[1])
    rh, L0, top = 30, lw, 70
    h = top + rh * len(items) + 60; mx = max([v for _, v, _ in items] + [1])
    xs = lambda v: L0 + v / mx * (w - L0 - 60)
    out = [svg_open(w, h), title_block(w, title, sub)]
    step = max(1, math.ceil(mx / 8))
    for v in range(0, int(mx) + step, step):
        out.append(L(xs(v), top - 6, xs(v), top + rh * len(items), "var(--grid)", 1)); out.append(T(xs(v), top + rh * len(items) + 18, fmt(v), anchor="middle"))
    for k, (lab, v, hov) in enumerate(items):
        y = top + k * rh
        maxc = max(6, int((L0 - 34) / 7.2)); full = str(lab)
        lab = full if len(full) <= maxc else full[:maxc - 1] + "…"
        lt = T(L0 - 10, y + rh / 2 + 5, lab, 13, "600", "end")
        if hov or lab != full: lt = lt.replace(">" + esc(lab) + "</text>", f"><title>{esc(hov or full)}</title>{esc(lab)}</text>")
        out.append(lt); out.append(R(xs(0), y + 5, xs(v) - xs(0), rh - 10, color, hov or lab, cls="hb"))
        out.append(T(xs(v) + 8, y + rh / 2 + 5, fmt(v) if isinstance(v, int) or float(v).is_integer() else f"{v:.2f}".rstrip("0").rstrip("."), 12, "600", fill="var(--muted)"))
    out.append(L(L0, top - 6, L0, top + rh * len(items))); out.append(L(L0, top + rh * len(items), w - 60, top + rh * len(items)))
    out.append(T((L0 + w - 60) / 2, h - 12, xlabel, 13, "bold", "middle"))
    if ylabel: out.append(T(14, (top + rh * len(items)) / 2, ylabel, 13, "bold", "middle", rot=True))
    return "".join(out) + "</svg>"

def stacked_h(title, rowsx, series, colmap, xlabel, ylabel, sub=None, w=900, unit="", total=True):
    """rowsx: [(label, {series: value})]"""
    rh, L0, top = 36, (215 if w >= 800 else 190), 70
    h = top + rh * len(rowsx) + 64; mx = max([sum(d.values()) for _, d in rowsx] + [1])
    xs = lambda v: L0 + v / mx * (w - L0 - 40)
    out = [svg_open(w, h), title_block(w, title, sub)]
    for v in range(0, int(mx) + 1, max(1, math.ceil(mx / 9))):
        out.append(L(xs(v), top - 8, xs(v), top + rh * len(rowsx), "var(--grid)", 1)); out.append(T(xs(v), top + rh * len(rowsx) + 18, v, 12, anchor="middle"))
    for k, (lab, d) in enumerate(rowsx):
        y = top + k * rh; x = 0
        maxc = max(6, int((L0 - 34) / 7.2)); full = str(lab); lab = full if len(full) <= maxc else full[:maxc - 1] + "…"
        out.append(T(L0 - 10, y + rh / 2 + 5, lab, 13, "600", "end").replace(">" + esc(lab) + "</text>", f"><title>{esc(full)}</title>{esc(lab)}</text>"))
        for sname in series:
            v = d.get(sname, 0)
            if not v: continue
            x0, x1 = xs(x), xs(x + v)
            out.append(R(x0, y + 5, x1 - x0, rh - 10, colmap[sname], "", cls="hb"))
            if x1 - x0 > 18: out.append(T((x0 + x1) / 2, y + rh / 2 + 5, f"{v}{unit}", 12, "bold", "middle", "var(--onbar)"))
            x += v
        if total: out.append(T(xs(x) + 8, y + rh / 2 + 5, x, 12, "600", fill="var(--muted)"))
        out.append(f"<rect class='hit' x='{L0:.0f}' y='{y:.0f}' width='{w - L0 - 40:.0f}' height='{rh:.0f}' fill='transparent' data-tip=\"{_tipdata(lab, d, series, colmap)}\"/>")
    out.append(L(L0, top - 8, L0, top + rh * len(rowsx))); out.append(L(L0, top + rh * len(rowsx), w - 40, top + rh * len(rowsx)))
    out.append(T((L0 + w - 40) / 2, h - 12, xlabel, 13, "bold", "middle")); out.append(T(14, (top + rh * len(rowsx)) / 2, ylabel, 13, "bold", "middle", rot=True))
    return "".join(out) + "</svg>"

def _tipdata(lab, d, series, colmap):
    tot = sum(d.get(k, 0) for k in series)
    rows_ = "".join(f"<div><span class=dot style='background:{colmap[k]}'></span>{esc(k)}<b>{(fmt(d[k]) if isinstance(d[k], int) or float(d[k]).is_integer() else f'{d[k]:.2f}'.rstrip('0').rstrip('.'))}</b></div>" for k in series if d.get(k))
    tt = (fmt(tot) if isinstance(tot, int) or float(tot).is_integer() else f"{tot:.2f}".rstrip("0").rstrip("."))
    return html.escape(f"<div class=tt-h>{esc(lab)}</div>{rows_}<div class=tt-t>total<b>{tt}</b></div>", quote=True)
def stacked_v(title, cats, series, colmap, xlabel, ylabel, sub=None, w=900, h=360):
    """cats: [(label, {series: value})] vertical stacked columns"""
    L0, top, B = 70, 70, 70; mx = max([sum(d.values()) for _, d in cats] + [0]) or 1
    cw = (w - L0 - 30) / max(1, len(cats)); ys = lambda v: top + (1 - v / mx) * (h - top - B)
    out = [svg_open(w, h), title_block(w, title, sub)]
    ticks = [mx * i / 5 for i in range(6)] if mx < 6 else list(range(0, int(mx) + 1, max(1, math.ceil(mx / 6))))
    for v in ticks:
        out.append(L(L0, ys(v), w - 30, ys(v), "var(--grid)", 1)); out.append(T(L0 - 8, ys(v) + 4, (f"{v:.2f}".rstrip("0").rstrip(".") if mx < 6 else fmt(v)), 12, anchor="end"))
    for k, (lab, d) in enumerate(cats):
        x = L0 + k * cw + cw * 0.15; acc = 0
        for sname in series:
            v = d.get(sname, 0)
            if not v: continue
            out.append(R(x, ys(acc + v), cw * 0.7, ys(acc) - ys(acc + v), colmap[sname], "", cls="vb")); acc += v
        out.append(f"<rect class='hit' x='{L0 + k * cw:.0f}' y='{top - 6:.0f}' width='{cw:.0f}' height='{h - B - top + 6:.0f}' fill='transparent' data-tip=\"{_tipdata(lab, d, series, colmap)}\"/>")
        if len(cats) > 8: out.append(f"<text x='{x + cw * 0.35:.0f}' y='{h - B + 8:.0f}' text-anchor='end' fill='var(--fg)' style='font-size:10px' transform='rotate(-45 {x + cw * 0.35:.0f} {h - B + 8:.0f})'>{esc(lab)}</text>")
        else: out.append(T(x + cw * 0.35, h - B + 16, lab, 11, anchor="middle"))
    out.append(L(L0, top - 6, L0, h - B)); out.append(L(L0, h - B, w - 30, h - B))
    out.append(T((L0 + w - 30) / 2, h - 12, xlabel, 13, "bold", "middle")); out.append(T(16, (top + h - B) / 2, ylabel, 13, "bold", "middle", rot=True))
    return "".join(out) + "</svg>"

def legend(colmap, header, counts=None):
    return f"<div class=legend><b>{esc(header)}</b>" + "".join(f"<div><span class=dot style='background:{c}'></span> {esc(k)}" + (f" <span class=muted>({counts[k]})</span>" if counts and k in counts else "") + "</div>" for k, c in colmap.items()) + "</div>"
def tip(t): return f"<span class=tip tabindex=0 data-tip='{esc(t)}'>?</span>"
def kpi(items):
    return "<div class=kpi>" + "".join(f"<div><b>{v}</b>{esc(k)}{tip(tp) if tp else ''}</div>" for k, v, tp in items) + "</div>"
def flex(chart, leg): return f"<div class=flex>{chart}{leg}</div>"
_tg = [0]
def toggle(options):
    """options: [(label, html)] -> segmented control that shows one at a time"""
    _tg[0] += 1; tid = f"tg{_tg[0]}"
    ctl = f"<div class='seg mini' data-tg='{tid}'>" + "".join(f"<button data-i='{i}' class='{'on' if i == 0 else ''}'>{esc(l)}</button>" for i, (l, _) in enumerate(options)) + "</div>"
    body = "".join(f"<div class='tgpane' data-tg='{tid}' data-i='{i}'{'' if i == 0 else ' style=\"display:none\"'}>{h}</div>" for i, (_, h) in enumerate(options))
    return f"<div class=tgwrap>{ctl}{body}</div>"
def cumul(cats):
    acc = collections.Counter(); out = []
    for lab, d in cats:
        acc.update(d); out.append((lab, dict(acc)))
    return out
def tseries(title, cats, series, colmap, xlabel, ylabel, sub=None, w=620, h=320, leg=None):
    """time series with a Per period / Cumulative switch"""
    a = stacked_v(title, cats, series, colmap, xlabel, ylabel, sub, w=w, h=h); b = stacked_v(title, cumul(cats), series, colmap, xlabel, ylabel, "Cumulative · running total across the range", w=w, h=h)
    if leg: a, b = flex(a, leg), flex(b, leg)
    return toggle([("Per period", a), ("Cumulative", b)])
def two(*items): return "<div class=two>" + "".join(f"<div class=cell>{it}</div>" for it in items) + "</div>"
def table(headers, body_rows, cls="sortable"):
    return f"<table class='{cls}'><tr>" + "".join(f"<th{' class=num' if h.startswith('#') else ''}>{esc(h.lstrip('#'))}</th>" for h in headers) + "</tr>" + "".join("<tr>" + "".join(f"<td{' class=num' if isinstance(v,(int,float)) else ''}>{v if isinstance(v,str) and v.startswith('<') else (fmt(v) if isinstance(v,int) and abs(v) >= 1000 else esc(v))}</td>" for v in r) + "</tr>" for r in body_rows) + "</table>"
def fmt(n):
    if not isinstance(n, (int, float)): return str(n)
    a = abs(n)
    if a >= 1e6: return f"{n/1e6:.1f}M"
    if a >= 1e3: return f"{n/1e3:.1f}k"
    return f"{n:.0f}" if float(n).is_integer() else f"{n:.2f}"

# ---------------- page ----------------
H = ["""<!doctype html><html lang="en" data-theme="dark"><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>Command Code dashboard</title><style>
:root{--font:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;--mono:ui-monospace,SFMono-Regular,Menlo,monospace}
[data-theme=dark]{--bg:#0b0e13;--surface:#12161d;--surface2:#181d26;--border:rgba(255,255,255,.08);--fg:#e8ebf0;--muted:#8b93a3;--grid:rgba(255,255,255,.07);--axis:rgba(255,255,255,.35);--bar:#c9d1dc;--bar2:#4a5262;--onbar:#0b0e13;--accent:#5b9cf6;--accent-fg:#0b0e13;--shadow:0 1px 0 rgba(255,255,255,.03) inset,0 8px 24px rgba(0,0,0,.35)}
[data-theme=light]{--bg:#f6f7f9;--surface:#ffffff;--surface2:#f1f3f6;--border:rgba(15,23,42,.10);--fg:#0f172a;--muted:#5b6472;--grid:rgba(15,23,42,.07);--axis:rgba(15,23,42,.45);--bar:#1f2937;--bar2:#b9c0cc;--onbar:#ffffff;--accent:#2563eb;--accent-fg:#fff;--shadow:0 1px 2px rgba(15,23,42,.06)}
*{box-sizing:border-box}html{background:var(--bg)}body{font:14px/1.5 var(--font);color:var(--fg);background:var(--bg);max-width:1240px;margin:0 auto;padding:28px 24px 80px;-webkit-font-smoothing:antialiased;font-variant-numeric:tabular-nums;text-wrap:pretty}
[data-theme=dark]{--glass:rgba(11,14,19,.72);--gloss:linear-gradient(180deg,rgba(255,255,255,.045),rgba(255,255,255,0) 38%);--edge:0 0 0 1px rgba(255,255,255,.06) inset,0 1px 0 rgba(255,255,255,.05) inset;--lift:0 10px 30px -12px rgba(0,0,0,.6),0 2px 6px rgba(0,0,0,.25)}
[data-theme=light]{--glass:rgba(246,247,249,.78);--gloss:linear-gradient(180deg,rgba(255,255,255,.9),rgba(255,255,255,0) 40%);--edge:0 0 0 1px rgba(15,23,42,.08) inset,0 1px 0 rgba(255,255,255,.8) inset;--lift:0 10px 28px -14px rgba(15,23,42,.25),0 1px 3px rgba(15,23,42,.06)}
a{color:var(--accent)}code{font-family:var(--mono);font-size:12px;background:var(--surface2);padding:1px 6px;border-radius:6px}
h1{font-size:22px;font-weight:650;letter-spacing:-.01em;margin:0 0 4px;text-wrap:balance}h2{font-size:16px;margin:32px 0 10px}h3{font-size:12px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);margin:30px 0 10px;scroll-margin-top:118px}
.sub{color:var(--muted);font-size:13px;margin:0 0 16px}.muted{color:var(--muted)}
.topbar{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap;margin:8px 0 4px}
button{font:inherit}button:active{scale:.96}button{transition-property:background-color,color,border-color,box-shadow,scale;transition-duration:.18s;transition-timing-function:cubic-bezier(.2,0,0,1)}
.seg{display:inline-flex;background:var(--surface2);border-radius:10px;padding:3px;box-shadow:var(--edge)}.seg button{border:0;background:transparent;color:var(--muted);padding:6px 12px;border-radius:7px;font-size:13px;cursor:pointer;min-height:32px}.seg button:hover{color:var(--fg)}.seg button.on{background:var(--surface);color:var(--fg);box-shadow:var(--lift)}
.tabs{position:sticky;top:0;z-index:5;background:var(--glass);-webkit-backdrop-filter:saturate(160%) blur(14px);backdrop-filter:saturate(160%) blur(14px);padding:10px 0 0;margin:8px 0 0;border-bottom:1px solid var(--border);box-shadow:0 12px 24px -16px rgba(0,0,0,.5)}.tabrow{display:flex;justify-content:space-between;align-items:flex-end;gap:12px}.tabbtns{display:flex;gap:2px}
.tabs .tabbtns button{border:0;background:transparent;color:var(--muted);padding:10px 16px;font-size:14px;cursor:pointer;border-bottom:2px solid transparent;margin-bottom:-1px;border-radius:8px 8px 0 0;min-height:40px}.tabs .tabbtns button:hover{color:var(--fg);background:var(--surface2)}.tabs .tabbtns button.on{color:var(--fg);border-bottom-color:var(--accent);font-weight:600}
.subnav{display:none;gap:4px;flex-wrap:wrap;padding:6px 0 8px;border-top:1px solid var(--border)}.subnav.on{display:flex}.subnav a{font-size:12px;color:var(--muted);text-decoration:none;padding:4px 11px;border-radius:999px;transition-property:background-color,color;transition-duration:.15s;min-height:26px}.subnav a:hover{color:var(--fg);background:var(--surface)}
.tab{display:none}.tab.on{display:block}.tabdesc{color:var(--muted);margin:14px 0 18px;font-size:14px}
.kpi{display:grid;grid-template-columns:repeat(auto-fit,minmax(128px,1fr));gap:10px;margin:0 0 16px}.kpi div{background:var(--surface);background-image:var(--gloss);border-radius:14px;padding:14px 16px;box-shadow:var(--edge),var(--lift);font-size:12px;color:var(--muted);letter-spacing:.02em;transition-property:translate,box-shadow;transition-duration:.2s;transition-timing-function:cubic-bezier(.2,0,0,1)}.kpi div:hover{translate:0 -2px}.kpi b{display:block;font-size:22px;font-weight:600;color:var(--fg);letter-spacing:-.01em;margin-bottom:2px}
.card{background:var(--surface);background-image:var(--gloss);border-left:3px solid var(--accent);border-radius:14px;padding:12px 16px;margin:0 0 16px;color:var(--fg);box-shadow:var(--edge),var(--lift)}
.chart{background:var(--surface);background-image:var(--gloss);border-radius:16px;box-shadow:var(--edge),var(--lift);margin:12px 0;display:block;width:100%}svg text{font-family:var(--font)}
.flex{display:flex;gap:16px;align-items:flex-start}.legend{flex:0 0 220px;background:var(--surface);background-image:var(--gloss);border-radius:16px;padding:12px 14px;font-size:13px;margin-top:12px;box-shadow:var(--edge),var(--lift)}.legend b{color:var(--muted);font-weight:600;font-size:11px;letter-spacing:.06em;text-transform:uppercase}.legend div{margin:8px 0}
.dot{display:inline-block;width:10px;height:10px;border-radius:5px;margin-right:6px;vertical-align:middle;box-shadow:0 0 0 1px rgba(0,0,0,.15) inset}
table{border-collapse:separate;border-spacing:0;width:100%;margin:8px 0 16px;background:var(--surface);background-image:var(--gloss);border-radius:14px;overflow:hidden;font-size:13px;box-shadow:var(--edge),var(--lift)}th{background:var(--surface2);color:var(--muted);font-weight:600;font-size:11px;letter-spacing:.05em;text-transform:uppercase;text-align:left;padding:9px 10px;border-bottom:1px solid var(--border)}td{padding:7px 10px;border-bottom:1px solid var(--border);vertical-align:top;transition-property:background-color;transition-duration:.12s}tr:last-child td{border-bottom:0}tr:hover td{background:var(--surface2)}.num{text-align:right}
.tf{display:flex;flex-wrap:wrap;gap:6px;margin:6px 0 10px}.tf button{border:0;background:var(--surface);color:var(--muted);border-radius:999px;padding:5px 11px;font-size:12px;cursor:pointer;box-shadow:var(--edge);min-height:28px}.tf button:hover{color:var(--fg)}
details summary{cursor:pointer;color:var(--accent)}blockquote{margin:8px 0;padding:8px 12px;border-left:2px solid var(--border);background:var(--surface);border-radius:0 10px 10px 0;color:var(--muted);font-size:13px}blockquote b{color:var(--fg)}
.grid2,.two{display:grid;grid-template-columns:1fr 1fr;gap:16px;align-items:stretch}.two .cell{display:flex;flex-direction:column;min-width:0}.two .cell>.chart,.two .cell>.flex{flex:1 1 auto}.two .cell>.flex>.chart{flex:1 1 auto;height:auto}.two .cell>.chart{height:auto}.two .cell>table{flex:1 1 auto;margin-top:12px}.chart{max-width:100%!important}
.two .flex{flex-direction:column;gap:0}.two .legend{flex:none;width:100%;margin-top:-4px;border-radius:0 0 16px 16px;padding:8px 14px;box-shadow:var(--edge)}.two .legend b{margin-right:12px}.two .legend div{display:inline-block;margin:4px 14px 4px 0}.two .flex .chart{margin-bottom:0;border-radius:16px 16px 0 0}.cell>.chart:first-child,.cell>.flex:first-child>.chart{margin-top:0}.cell>.flex:first-child{margin-top:12px}.full{grid-column:1/-1}
.two .cell>.chart,.two .cell>.flex>.chart{height:100%;min-height:0}
@media(max-width:900px){.grid2,.two{grid-template-columns:1fr}.flex{flex-direction:column}.legend{flex:none}}
ul{padding-left:18px}li{margin:3px 0}
.tip{display:inline-flex;align-items:center;justify-content:center;width:16px;height:16px;border-radius:8px;box-shadow:var(--edge);color:var(--muted);font-size:10px;margin-left:6px;cursor:help;position:relative;vertical-align:middle}.tip::after{content:attr(data-tip);position:absolute;left:50%;bottom:22px;transform:translateX(-50%) translateY(4px);width:280px;background:var(--surface2);color:var(--fg);border-radius:10px;padding:8px 10px;font-size:12px;line-height:1.4;font-weight:400;text-align:left;white-space:normal;z-index:20;box-shadow:var(--edge),var(--lift);opacity:0;pointer-events:none;transition-property:opacity,transform;transition-duration:.16s;transition-timing-function:cubic-bezier(.2,0,0,1)}.tip:hover::after,.tip:focus::after{opacity:1;transform:translateX(-50%) translateY(0)}.kpi div .tip{float:right}.charttip{margin:-6px 0 10px;font-size:12px;color:var(--muted)}
.filters{display:flex;flex-wrap:wrap;gap:12px;align-items:center;margin:8px 0}.filters label{font-size:12px;color:var(--muted)}.filters select,.filters input{margin-left:6px;background:var(--surface);color:var(--fg);border:0;box-shadow:var(--edge);border-radius:8px;padding:6px 9px;font:inherit;font-size:13px;min-height:32px}.pager{display:flex;gap:12px;align-items:center;margin:4px 0 16px}.pager button{background:var(--surface);color:var(--fg);border:0;box-shadow:var(--edge);border-radius:8px;padding:6px 12px;cursor:pointer;min-height:32px}.pager button:disabled{opacity:.4;cursor:default}
table.sortable th,table th[data-k]{cursor:pointer;user-select:none}table.sorted td{color:var(--muted)}table.sorted td.sortcol{color:var(--fg);font-weight:600}table.sorted th.asc,table.sorted th.desc{color:var(--accent)}th.asc::after{content:' ▲';font-size:9px}th.desc::after{content:' ▼';font-size:9px}
.tgwrap{position:relative}.tgwrap>.tgpane>.chart,.tgwrap>.tgpane>.flex>.chart{margin-top:12px}.seg.mini{position:absolute;right:14px;top:22px;z-index:3;padding:2px}.tgwrap .tgwrap .seg.mini{top:54px}.seg.mini button{padding:3px 9px;font-size:12px;min-height:26px}
.tt{position:fixed;z-index:50;pointer-events:none;opacity:0;background:var(--surface2);color:var(--fg);border-radius:10px;padding:8px 10px;font-size:12px;line-height:1.5;box-shadow:var(--edge),var(--lift);min-width:160px;transition-property:opacity;transition-duration:.12s}.tt .dot{margin-right:6px}.tt div{display:flex;justify-content:space-between;gap:14px}.tt b{margin-left:auto}.tt .tt-h{font-weight:600;margin-bottom:4px;color:var(--fg)}.tt .tt-t{border-top:1px solid var(--border);margin-top:4px;padding-top:4px;color:var(--muted)}
rect.hit{pointer-events:all}
.reveal{opacity:0;translate:0 12px;transition-property:opacity,translate;transition-duration:.55s;transition-timing-function:cubic-bezier(.2,0,0,1)}.reveal.seen{opacity:1;translate:0 0}
/* motion: staggered entrance when a tab activates, bars grow in; skipped on first paint and under reduced motion */
@keyframes rise{from{opacity:0;translate:0 8px}to{opacity:1;translate:0 0}}@keyframes growx{from{transform:scaleX(0)}to{transform:scaleX(1)}}@keyframes growy{from{transform:scaleY(0)}to{transform:scaleY(1)}}
.tab.on.anim>*{animation:rise .42s cubic-bezier(.2,0,0,1) both}.tab.on.anim>*:nth-child(2){animation-delay:60ms}.tab.on.anim>*:nth-child(3){animation-delay:120ms}.tab.on.anim>*:nth-child(4){animation-delay:180ms}.tab.on.anim>*:nth-child(5){animation-delay:240ms}.tab.on.anim>*:nth-child(n+6){animation-delay:300ms}
.tab.on.anim rect.hb{transform-box:fill-box;transform-origin:left center;animation:growx .6s cubic-bezier(.2,0,0,1) both;animation-delay:.15s}.tab.on.anim rect.vb{transform-box:fill-box;transform-origin:center bottom;animation:growy .6s cubic-bezier(.2,0,0,1) both;animation-delay:.15s}
@media(prefers-reduced-motion:reduce){.tab.on.anim>*,.tab.on.anim rect.hb,.tab.on.anim rect.vb{animation:none}button:active{scale:1}.reveal{opacity:1;translate:0 0;transition:none}}
</style>"""]

ROWS_ALL = rows
ALL_SESSIONS = sessions
_pm_all = collections.Counter()
for s_ in sessions: _pm_all.update(s_["models"])
ALL_MODELS = [m for m, _ in _pm_all.most_common()]
# Color scheme: one hue per provider, shades per model within the provider. Stable for the whole page.
_HUES = [150, 215, 35, 0, 265, 330, 185, 70, 300, 100, 20, 240]
def _provider(m):
    m = m.lower()
    if "/" in m: return m.split("/")[0]
    for pfx in ("gpt", "o1", "o3", "o4"): 
        if m.startswith(pfx): return "openai"
    if m.startswith("claude"): return "anthropic"
    if m.startswith("gemini"): return "google"
    return m.split("-")[0]
_prov_order = []
for m in ALL_MODELS:
    pv = _provider(m)
    if pv not in _prov_order: _prov_order.append(pv)
def model_color(m):
    pv = _provider(m); hue = _HUES[_prov_order.index(pv) % len(_HUES)] if pv in _prov_order else 210
    sibs = [x for x in ALL_MODELS if _provider(x) == pv]; k = sibs.index(m) if m in sibs else 0
    light = [55, 68, 42, 78, 34][k % 5]; sat = 62 if k < 5 else 45
    return f"hsl({hue} {sat}% {light}%)"
ALL_MCOLS = {m: model_color(m) for m in ALL_MODELS}
PROVIDER_COLS = {pv: f"hsl({_HUES[i % len(_HUES)]} 62% 55%)" for i, pv in enumerate(_prov_order)}
def build(SUF, rows, sessions, acts, rows_r=None, gran="day", cut="0000"):
    rows_r = rows if rows_r is None else rows_r
    def _local(ts):
        try: return datetime.datetime.fromisoformat(ts.rstrip("Z")).replace(tzinfo=datetime.timezone.utc).astimezone()
        except Exception: return None
    # time buckets adapt to the range: 24h -> 1h, 7d -> 6h, 30d -> 1 day, all -> 1 week (local time)
    SIZE_H = {"hour": 1, "6h": 6, "day": 24, "week": 168}[gran]
    BUCKET_WORD = {"hour": "hour", "6h": "6 hours", "day": "day", "week": "week"}[gran]
    def hour_buckets(size_h=None, n=None):
        size_h = size_h or SIZE_H
        now_l = datetime.datetime.now()
        if size_h == 1: end = now_l.replace(minute=0, second=0, microsecond=0) + datetime.timedelta(hours=1); n = n or 24
        elif size_h == 6: end = now_l.replace(hour=(now_l.hour // 6) * 6, minute=0, second=0, microsecond=0) + datetime.timedelta(hours=6); n = n or 28
        elif size_h == 24: end = now_l.replace(hour=0, minute=0, second=0, microsecond=0) + datetime.timedelta(days=1); n = n or 30
        else:
            end = (now_l.replace(hour=0, minute=0, second=0, microsecond=0) - datetime.timedelta(days=now_l.weekday())) + datetime.timedelta(days=7)
            first_d = datetime.date.fromisoformat(min(s_["date"] for s_ in sessions)) if sessions else now_l.date()
            n = n or max(2, math.ceil(((end.date() - first_d).days) / 7))
        out = []
        for i in range(n, 0, -1):
            st = end - datetime.timedelta(hours=size_h * i)
            lbl = st.strftime("%H:00") if size_h == 1 else (st.strftime("%a %H:00") if size_h == 6 else (st.strftime("%b %d") if size_h == 24 else "wk " + st.strftime("%b %d")))
            out.append((lbl, st.astimezone(datetime.timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%S")))
        return out
    def by_hour(items, key, size_h=None, n=None):
        B = hour_buckets(size_h, n); lbls = [b[0] for b in B]; starts = [b[1] for b in B]
        res = collections.OrderedDict((l, collections.Counter()) for l in lbls)
        for it in items:
            t = key(it)
            if not t: continue
            idx = bisect.bisect_right(starts, t) - 1
            if 0 <= idx < len(lbls): res[lbls[idx]][it.get("_s", "n")] += it.get("_v", 1)
        return res
    PER = f"per {BUCKET_WORD}"
    last_ts = max([t["ts"] for s_ in ALL_SESSIONS for t in s_["turns"]] + ["0"])
    last_local = _local(last_ts) if last_ts != "0" else None
    ago_min = round((datetime.datetime.now(datetime.timezone.utc) - last_local).total_seconds() / 60) if last_local else None
    H = []
    dc_all = collections.Counter(r["domain"] for r in rows)
    KEEP = {d for d, _ in dc_all.most_common(3)}
    rows_main = [r for r in rows if r["domain"] in KEEP]
    tc = collections.Counter(t for r in rows_main for t in r["traits"])
    dc = collections.Counter(r["domain"] for r in rows_main)
    tl = [t for t, _, _ in sorted(TRAITS, key=lambda x: -tc.get(x[0], 0))]
    dl = [d for d, _ in dc.most_common()]
    PAL = ["#3ecf8e", "#5b9cf6", "#f5a524", "#ef6b6b", "#a78bfa", "#e879a8", "#22c9d6", "#c8d64b", "#9aa3b2"]
    cols = {d: PAL[i] for i, d in enumerate(dl)}

    # ---------------- activation aggregates ----------------
    act_by_b = collections.Counter(a["b"] for a in acts)
    steer_by_b = collections.Counter(a["b"] for a in acts if a["steer"])
    act_by_trait = collections.Counter(t for a in acts for t in ROWS_ALL[a["b"]]["traits"])
    act_by_domain = collections.Counter(ROWS_ALL[a["b"]]["domain"] for a in acts)
    act_by_day = collections.Counter(a["date"] for a in acts)
    never = [r for r in rows if act_by_b[r["i"] - 1] == 0]
    for r in rows: r["acts"] = act_by_b[r["i"] - 1]; r["steers"] = steer_by_b[r["i"] - 1]


    TABS = [("Overview", "Cost, speed and taste at a glance"), ("Taste", "What the file says about you"), ("Influence", "When taste steps in"), ("Models", "How each model spends and behaves"), ("Usage", "Sessions, tools and prompts"), ("Health", "Is the taste file in good shape")]
    cur = [None]
    def tab(name):
        if cur[0]: H.append("</div>")
        cur[0] = name; desc = dict(TABS)[name]
        H.append(f"<div class=tab id='tab-{name}{SUF}'><p class=tabdesc>{esc(desc)}.</p>")
    SECS = collections.OrderedDict(); _sec = [0]
    def sec_id(t): _sec[0] += 1; return f"s{_sec[0]}{SUF}"
    def h3(t, target=None):
        i = sec_id(t); (target if target is not None else H).append(f"<h3 id='{i}'>{esc(t)}</h3>"); SECS.setdefault(cur[0] or "Overview", []).append((i, t))
    tot_cost = sum(s["cost"] for s in sessions); tot_in = sum(s["inp"] for s in sessions); tot_cr = sum(s["cr"] for s in sessions); tot_out = sum(s["out"] for s in sessions)
    est_tokens = len(raw) // 4; TOK_NOTE = "estimated as bytes ÷ 4, about ±20%"
    latest_in = next((s["first_in"] for s in reversed(sessions) if s["first_in"]), 1)
    n_steer = sum(1 for a in acts if a["steer"])
    OV = {}
    pm = collections.defaultdict(collections.Counter)
    for s in sessions:
        for m, c in s["pm"].items(): pm[m].update(c)
    ml = sorted(pm, key=lambda m: -pm[m]["asst"]); mcols = {m: ALL_MCOLS.get(m, model_color(m)) for m in ml}

    # ================= TASTE =================
    tab("Taste")
    H.append(kpi([("bullets in file", len(rows), "One bullet = one learned preference or fact, stored as a line in taste.md. The whole file is injected regardless of range."), ("learned in this range", len(rows_r), "Bullets whose learned-from session falls inside the selected range."), ("est. tokens", fmt(est_tokens), "Bytes ÷ 4, about ±20%. Sent with every request."), ("median confidence", f"{sorted(r['conf'] for r in rows)[len(rows)//2]:.2f}", "The learner attaches a 0 to 1 confidence to each bullet. Higher means it saw the preference repeated or stated explicitly."), ("sessions learned from · " + ", ".join(f'{k} {v}' for k, v in learned.items()), sum(learned.values()), "Sessions from other coding agents that cmd mined to build this file.")]))
    rows_main_r = [r for r in rows_r if r["domain"] in KEEP]; dc_r = collections.Counter(r["domain"] for r in rows_main_r)
    grid = [(t, {d: sum(1 for r in rows_main_r if r["domain"] == d and t in r["traits"]) for d in dl}) for t in tl]
    habits_chart = flex(stacked_h("Work habits by work area", grid, dl, cols, "Number of bullets", "Work habit", "Bullets learned in this range. Each bar is one habit; colors show the work area.", w=620), legend(cols, "Work area", dc_r))
    habit_defs = "<p class=muted>A bullet can show more than one habit." + (f" Too small to chart: {', '.join(f'{d} ({k})' for d, k in dc_all.items() if d not in KEEP)}." if len(dc_all) > len(dc) else "") + "</p><ul>" + "".join(f"<li><b>{t}</b>: {d}</li>" for t, d, _ in TRAITS) + "</ul>"
    d0 = min(datetime.date.fromisoformat(r["date"]) for r in rows); d1 = max(datetime.date.fromisoformat(r["date"]) for r in rows); span = max(1, (d1 - d0).days)
    if not rows_r: H.append("<p class=muted style='padding:12px 0'>No bullets were learned in this range. Widen the range to see the file's content; the whole file is still injected into every prompt.</p>")
    else: H.append(two(habits_chart, habit_defs))
    OV["habits"] = flex(stacked_h("Work habits by work area", [(t, {d: sum(1 for r in rows_main if r["domain"] == d and t in r["traits"]) for d in dl}) for t in tl], dl, cols, "Number of bullets", "Work habit", "Whole taste file. Each bar is one habit; colors show the work area.", w=620), legend(cols, "Work area", dc))
    h3("Where the bullets came from")
    srcs = collections.Counter(r["src"] for r in rows_r); sl = [k for k, _ in srcs.most_common()]
    scols = {k: (ALL_MCOLS.get(k[6:], model_color(k[6:])) if k.startswith("cmd · ") else {"Claude Code": "hsl(210 10% 62%)", "Cursor": "hsl(40 8% 58%)"}.get(k, "hsl(210 8% 40%)")) for k in sl}
    weeks_b = collections.OrderedDict()
    for r in sorted(rows_r, key=lambda r: r["date"]):
        wk = (datetime.date.fromisoformat(r["date"]) - datetime.timedelta(days=datetime.date.fromisoformat(r["date"]).weekday())).isoformat()
        weeks_b.setdefault(wk, collections.Counter())[r["src"]] += 1
    H.append("<p class=charttip>Each bullet is matched to the session it was most likely learned from by shared distinctive words. <b>Unmatched</b> = no transcript on disk shared enough distinctive words: the source session was deleted or compacted, or the learner paraphrased the bullet beyond recognition.</p>")
    if rows_r: H.append(two(hbars("Bullets by source", [(k, v, "") for k, v in srcs.items()], "Bullets", ylabel="Source", w=620, lw=230),
                 tseries("Bullets learned per week, by source", [(wk[5:], dict(c)) for wk, c in weeks_b.items()], sl, scols, "Week starting", "Bullets", w=620, h=380, leg=legend(scols, "Source", srcs))))
    h3("All bullets")
    H.append("<p class=muted>Activations = times matched in the model's thinking; steering = of those, changed the plan. Click a column header to sort.</p>")
    bdata = [dict(i=r["i"], area=r["domain"], habits=r["traits"], conf=r["conf"], date=r["date"], src=r["src"], acts=r["acts"], steers=r["steers"], text=r["text"]) for r in rows_r]
    areas = sorted(set(r["domain"] for r in rows_r)); habs = [t for t, _, _ in TRAITS]
    dr0 = min([r["date"] for r in rows_r] or [d0.isoformat()]); dr1 = max([r["date"] for r in rows_r] or [d1.isoformat()])
    H.append(f"<div class=filters id='bf{SUF}'><label>Area <select data-k='area'><option value=''>all</option>" + "".join(f"<option>{esc(a)}</option>" for a in areas) + "</select></label>"
             "<label>Habit <select data-k='habit'><option value=''>all</option>" + "".join(f"<option>{esc(t)}</option>" for t in habs) + "</select></label>"
             f"<label>From <input type=date data-k='from' value='{dr0}'></label><label>To <input type=date data-k='to' value='{dr1}'></label>"
             "<label>Search <input type=search data-k='q' placeholder='text…'></label><span class=muted data-k='count'></span></div>")
    H.append(f"<div id='bt{SUF}'></div><div class=pager id='bp{SUF}'></div>")
    H.append("<script>(function(){const D=" + json.dumps(bdata) + ";const S='" + SUF + "';const f=document.getElementById('bf'+S),t=document.getElementById('bt'+S),p=document.getElementById('bp'+S);"
             "const st={area:'',habit:'',from:f.querySelector('[data-k=from]').value,to:f.querySelector('[data-k=to]').value,q:'',sort:'date',asc:false,page:0,size:25};"
             "const cols=[['i','#'],['area','Area'],['habits','Habits'],['conf','Conf'],['date','Date'],['src','Source'],['acts','Activations'],['steers','Steering'],['text','Text']];"
             "function rows(){return D.filter(r=>(!st.area||r.area===st.area)&&(!st.habit||r.habits.includes(st.habit))&&r.date>=st.from&&r.date<=st.to&&(!st.q||r.text.toLowerCase().includes(st.q))).sort((a,b)=>{let x=a[st.sort],y=b[st.sort];if(Array.isArray(x)){x=x.join();y=y.join()}if(x<y)return st.asc?-1:1;if(x>y)return st.asc?1:-1;return a.i-b.i;});}"
             "function esc(s){return String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}"
             "function render(){const R=rows();const n=Math.max(1,Math.ceil(R.length/st.size));st.page=Math.min(st.page,n-1);const P=R.slice(st.page*st.size,(st.page+1)*st.size);"
             "const numk=['i','conf','acts','steers'];let lo=0,hi=0;if(st._k&&numk.includes(st._k)){const vs=R.map(r=>r[st._k]);lo=Math.min(...vs);hi=Math.max(...vs);}const cell=(k,v,cls)=>{let st_='';if(st._k===k){cls+=' sortcol';if(numk.includes(k)&&hi>lo)st_=` style=\"background:color-mix(in srgb, var(--accent) ${Math.round(6+44*(v-lo)/(hi-lo))}%, transparent)\"`;}return `<td class='${cls}'${st_}>`;};t.innerHTML='<table class=\"'+(st._k?'sorted':'')+'\"><tr>'+cols.map(([k,l])=>`<th data-k='${k}' class='${['i','conf','acts','steers'].includes(k)?'num':''} ${st._k===k?(st.asc?'asc':'desc'):''}'>${l}</th>`).join('')+'</tr>'+P.map(r=>`<tr>${cell('i',r.i,'num')}${r.i}</td>${cell('area',0,'')}${esc(r.area)}</td>${cell('habits',0,'')}${esc(r.habits.join(', ')||'—')}</td>${cell('conf',r.conf,'num')}${r.conf.toFixed(2)}</td>${cell('date',0,'')}${r.date}</td>${cell('src',0,'')}${esc(r.src)}</td>${cell('acts',r.acts,'num')}${r.acts}</td>${cell('steers',r.steers,'num')}${r.steers}</td>${cell('text',0,'')}${esc(r.text)}</td></tr>`).join('')+'</table>';"
             "t.querySelectorAll('th').forEach(h=>h.onclick=()=>{const k=h.dataset.k;const textish=k==='text'||k==='area'||k==='habits'||k==='src';if(st._k!==k){st.sort=k;st.asc=textish;st._k=k;st._n=1;}else if(st._n===1){st.asc=!st.asc;st._n=2;}else{st.sort='date';st.asc=false;st._k=undefined;st._n=0;}render();});"
             "f.querySelector('[data-k=count]').textContent=R.length+' bullets';p.innerHTML=`<button ${st.page===0?'disabled':''} data-d='-1'>‹ Prev</button><span>Page ${st.page+1} of ${n}</span><button ${st.page>=n-1?'disabled':''} data-d='1'>Next ›</button>`;p.querySelectorAll('button').forEach(b=>b.onclick=()=>{st.page+=+b.dataset.d;render();});}"
             "f.querySelectorAll('select,input').forEach(el=>el.oninput=()=>{st[el.dataset.k]=el.dataset.k==='q'?el.value.toLowerCase():el.value;st.page=0;render();});render();})();</script>")

    # ================= INFLUENCE =================
    tab("Influence")
    H.append(kpi([("activations", len(acts), "Moments where the model's reasoning mentioned taste and could be matched to one specific bullet."), ("steering", n_steer, "Activations where the sentence went on to change the plan: so, should, instead, before, never."), ("steering share", f"{100*n_steer/max(1,len(acts)):.0f}%", "Of all activations, the share that changed what the model did next."), ("turns that consulted taste", f"{100*sum(s['turns_cite'] for s in sessions)/max(1,sum(s['asst'] for s in sessions)):.1f}%", "Share of all assistant turns whose reasoning mentioned taste at all."), ("bullets consulted", len(rows) - len(never), f"Distinct bullets referenced at least once, out of {len(rows)}."), ("skill invocations", sum(1 for e in skill_events if e["sid"] in {s["sid"] for s in sessions}), "Explicit activate_skill calls, for comparison with implicit taste use.")]))
    hb = [(t, {"steering": sum(1 for a in acts if a["steer"] and t in ROWS_ALL[a["b"]]["traits"]), "mention": sum(1 for a in acts if not a["steer"] and t in ROWS_ALL[a["b"]]["traits"])}) for t in tl]
    c1 = flex(stacked_h("Activations by work habit", hb, ["steering", "mention"], {"steering": "var(--bar)", "mention": "var(--bar2)"}, "Activations", "Work habit", "Dark = the thought changed the plan. Light = taste was only mentioned.", w=620), legend({"steering": "var(--bar)", "mention": "var(--bar2)"}, "Kind"))
    tot_t = sum(tc.values()) or 1; tot_a = sum(act_by_trait.values()) or 1
    c2 = (hbars("How hard each habit's bullets work", [(t, round(act_by_trait.get(t, 0) / max(1, tc.get(t, 0)), 1), f"{act_by_trait.get(t,0)} activations across {tc.get(t,0)} bullets") for t in tl], "Activations per bullet", "Average times a bullet of this habit was consulted. Low = dead weight in the prompt.", ylabel="Work habit", w=620, lw=190))
    H.append(two(c1, c2)); OV["infl"] = c1; OV["work"] = c2
    top_b = sorted(rows, key=lambda r: -r["acts"])[:15]
    c3 = hbars("Most activated bullets", [(f"#{r['i']} " + r["text"][:28] + "…", r["acts"], r["text"]) for r in top_b], "Activations", "Hover a bar for the full bullet", ylabel="Bullet", lw=260, w=620)
    days = sorted(set(a["date"] for a in acts))
    if True:
        hb_ = by_hour([dict(_s="steering" if a["steer"] else "mention", ts=a["ts"]) for a in acts], lambda a: a["ts"][:19])
        c4 = tseries(f"Activations {PER} (local time)", [(l, dict(c)) for l, c in hb_.items()], ["steering", "mention"], {"steering": "var(--bar)", "mention": "var(--bar2)"}, BUCKET_WORD.capitalize(), "Activations", w=620, h=520)
    if False: c4 = "" if not days else (stacked_v("Activations per day", [(dd[5:], {"steering": sum(1 for a in acts if a["date"] == dd and a["steer"]), "mention": sum(1 for a in acts if a["date"] == dd and not a["steer"])}) for dd in days], ["steering", "mention"], {"steering": "var(--bar)", "mention": "var(--bar2)"}, "Day", "Activations", w=620, h=520))
    H.append(two(c3, c4))
    pc = sum(s["push_after_cite"] for s in sessions); pn = sum(s["push_after_nocite"] for s in sessions); tcn = sum(s["turns_cite"] for s in sessions); tnn = sum(s["turns_nocite"] for s in sessions)
    h3("Does the user push back less after taste-guided turns?")
    H.append(table(["Preceding assistant turn", "#Turns", "#User pushbacks after", "#Pushback rate %"], [("consulted taste", tcn, pc, round(100 * pc / max(1, tcn), 2)), ("did not", tnn, pn, round(100 * pn / max(1, tnn), 2))]))
    H.append(f"<p class=muted>Pushback = a user message containing unacceptable / wrong / revert / that's not / why did you. Only {sum(s['user'] for s in sessions)} user prompts exist, so this is a weak hint, and the only outcome-like signal on disk.</p>")
    h3("Skills alongside taste")
    sk = collections.Counter(e["skill"] for e in skill_events if e["sid"] in {s["sid"] for s in sessions})
    H.append("<p class=charttip>Skills are explicit playbooks loaded on demand; taste is implicit and always present.</p>")
    if sk:
        sk_rows = [(s["date"], f"{s['sid']} {s['title']}", s["model"][:24], ", ".join(f"{k}×{v}" if v > 1 else k for k, v in s["skills"].most_common()) or "—", sum(s["skills"].values()), s["cites"], s["steer"]) for s in sessions if s["skills"] or s["cites"]]
        H.append(two(hbars("Skill invocations", [(k, v, "") for k, v in sk.items()], "Invocations", f"{sum(sk.values())} invocations across {sum(1 for s in sessions if s['skills'])} sessions", ylabel="Skill", w=620, lw=230),
                     table(["Date", "Session", "Model", "Skills used", "#Skill calls", "#Taste activations", "#Steering"], sk_rows)))
    else: H.append("<p class=muted>No skill invocations in this range.</p>")
    h3("What steering looks like"); H.append("<details><summary>Show up to 25 steering moments</summary>")
    for dd, sid, b, q in steer_quotes[:25]: H.append(f"<blockquote><b>{dd} · {sid} · bullet #{b+1}</b><br>{esc(q)}</blockquote>")
    H.append("</details>")
    if learn_events: H.append(f"<p><b>In-session learn events</b> (the agent called the taste tool): {', '.join(f'{d} ({s})' for d, s in learn_events)}.</p>")

    # ================= MODELS =================
    tab("Models")
    ma = collections.Counter(a["model"] for a in acts); ms = collections.Counter(a["model"] for a in acts if a["steer"])
    def med(xs): xs = sorted(xs); return xs[len(xs)//2] if xs else 0
    tps_med = {m: med([v for mm, v in tps_samples if mm == m]) for m in ml}
    H.append(kpi([("models used", len(ml), "Distinct models that produced at least one assistant turn."), ("output tok/s (median)", round(med([v for _, v in tps_samples]), 1), "Output tokens divided by turn duration, median across all timed turns. Network latency is still inside the clock."), ("output tok/turn", fmt(round(tot_out / max(1, sum(s['asst'] for s in sessions)))), "Average output tokens per assistant turn, reasoning included."), ("thinking chars/turn", fmt(round(sum(pm[m]["think"] for m in ml) / max(1, sum(s['asst'] for s in sessions)))), "Visible reasoning produced per turn. More reasoning leaves more room to consult taste."), ("output tokens", fmt(tot_out), "Everything the models generated, reasoning included.")]))
    H.append("<p class=charttip>Attributed per message from each reply's own model and usage record. Output tok/s is the wall-clock speed you waited for, median per turn. Free tiers log $0.</p>")
    H.append(table(["Model", "#Turns", "#Input tokens", "#Cache hit %", "#Output tokens", "#Cost $", "#Input tok/turn", "#Output tok/turn", "#Output tok/s", "#Thinking chars/turn", "#Activations /100 turns", "#Steering share %"], [(m, pm[m]["asst"], fmt(pm[m]["inp"]), round(100 * pm[m]["cr"] / max(1, pm[m]["inp"])), fmt(pm[m]["out"]), round(pm[m]["cost"], 2), fmt(round(pm[m]["inp"] / max(1, pm[m]["asst"]))), fmt(round(pm[m]["out"] / max(1, pm[m]["asst"]))), round(tps_med[m], 1), round(pm[m]["think"] / max(1, pm[m]["asst"])), round(100 * ma[m] / max(1, pm[m]["asst"]), 1), round(100 * ms[m] / max(1, ma[m]))) for m in ml]))
    H.append(two(hbars("Output speed by model", [(m, round(tps_med[m], 1), f"median of {sum(1 for mm, _ in tps_samples if mm == m)} timed turns") for m in ml], "Output tokens per second (median turn)", "Wall-clock speed you experienced, median turn", ylabel="Model", lw=230, w=620),
                 hbars("Output tokens per turn by model", [(m, round(pm[m]["out"] / max(1, pm[m]["asst"])), "") for m in ml], "Output tokens per assistant turn", "Reasoning plus visible text", ylabel="Model", lw=230, w=620)))
    OV["speed"] = hbars("Output speed by model", [(m, round(tps_med[m], 1), f"median of {sum(1 for mm, _ in tps_samples if mm == m)} timed turns") for m in ml], "Output tokens per second (median turn)", "Wall-clock speed you experienced, median turn", ylabel="Model", lw=230, w=620)
    OV["cost"] = hbars("Cost by model", [(m, round(pm[m]["cost"], 2), "") for m in ml], "USD", "Logged by the provider; free tiers show 0", ylabel="Model", lw=230, w=620)
    OV["model"] = hbars("Taste activations per 100 turns, by model", [(m, round(100 * ma[m] / max(1, pm[m]["asst"]), 1), f"{ma[m]} activations over {pm[m]['asst']} turns") for m in ml], "Activations per 100 assistant turns", "How often each model consults the taste file", ylabel="Model", lw=230, w=620)
    # ---- two-way influence: model -> harness (bullets written) vs harness -> model (activations) ----
    written = collections.Counter()
    for r in rows_r:
        if r["src"].startswith("cmd · "): written[r["src"][6:]] += 1
    tw_rows = []
    for m in ml:
        t_ = pm[m]["asst"]; w_ = written.get(m, 0); a_ = ma[m]
        wr_ = 100 * w_ / max(1, t_); cr_ = 100 * a_ / max(1, t_)
        bal = round(100 * cr_ / (wr_ + cr_)) if (wr_ + cr_) > 0 else "—"
        tw_rows.append((m, t_, w_, round(wr_, 2), a_, round(cr_, 1), round(100 * ms[m] / max(1, a_)), round(wr_ + cr_, 1), bal))
    h3("Does the model teach taste, or use it?")
    H.append("<p class=charttip>Two directions, both within the selected range. <b>Creates</b>: new taste bullets learned from this model's sessions. <b>Uses</b>: times this model's reasoning consulted a bullet. Both per 100 turns so long and short sessions compare fairly.</p>")
    H.append(table(["Model", "#Turns", "#New bullets created", "#Created per 100 turns", "#Times taste used", "#Used per 100 turns", "#Uses that changed the plan %", "#Taste traffic per 100 turns", "#Share of traffic that is use %"], tw_rows))
    H.append("<p class=charttip><b>Taste traffic</b> = created + used, per 100 turns: how much this model interacts with taste at all. <b>Share that is use</b>: 0% = the model only creates bullets, 100% = it only uses them, 50% = balanced.</p>")
    bal_items = [(m, round(100 * (100 * ma[m] / max(1, pm[m]["asst"])) / max(1e-9, 100 * ma[m] / max(1, pm[m]["asst"]) + 100 * written.get(m, 0) / max(1, pm[m]["asst"])), 0) if (ma[m] + written.get(m, 0)) else 0, f"{written.get(m,0)} written, {ma[m]} consulted") for m in ml if pm[m]["asst"] >= 10]
    # sankey: where taste comes from and who uses it
    src_in = collections.Counter(r["src"] for r in rows_r); cons = collections.Counter(a["model"] for a in acts)
    def sankey(src_in, cons, w=1240, h=500):
        Lx, Rx, Mx, nw, gap, top, bot = 60, w - 60, w / 2, 14, 10, 96, 30
        H_ = h - top - bot
        def layout(counter, x):
            tot = sum(counter.values()) or 1; y = top; nodes = {}
            usable = H_ - gap * (len(counter) - 1)
            for k, v in counter.most_common():
                hh = usable * v / tot; nodes[k] = (x, y, hh, v); y += hh + gap
            return nodes
        Ln = layout(src_in, Lx); Rn = layout(cons, Rx - nw)
        mid_h = H_ * 0.9; mid_y = top + (H_ - mid_h) / 2
        out = [svg_open(w, h), title_block(w, "Taste flow: where bullets come from, and which models use them", "Both sides follow the selected range. Each side is scaled to its own total, so widths are shares within a side, not per-turn rates; see the chart below for rates per 100 turns.")]
        tot_in, tot_out = sum(src_in.values()), sum(cons.values())
        out.append(T(Lx, top - 30, "IN · bullets created", 13, "bold")); out.append(T(Lx, top - 14, f"{tot_in} bullets learned in this range, by the session that taught them", 11, fill="var(--muted)"))
        out.append(T(Rx, top - 30, "OUT · bullets consulted", 13, "bold", "end")); out.append(T(Rx, top - 14, f"{tot_out} consultations in this range, by the model that used them", 11, fill="var(--muted)", anchor="end"))
        out.append(T(18, top + H_ / 2, "Source of bullets", 12, "bold", "middle", rot=True)); out.append(T(w - 14, top + H_ / 2, "Model using bullets", 12, "bold", "middle", rot=True))
        neutral = {"Claude Code": "hsl(210 10% 62%)", "Cursor": "hsl(40 8% 58%)", "unmatched": "hsl(210 8% 40%)"}
        scol = {k: (ALL_MCOLS.get(k[6:], model_color(k[6:])) if k.startswith("cmd · ") else neutral.get(k, "var(--muted)")) for k in src_in}
        out.append(R(Mx - nw / 2, mid_y, nw, mid_h, "var(--fg)", f"taste.md · {sum(src_in.values())} bullets in, {sum(cons.values())} consultations out", 0.9))
        out.append(T(Mx, mid_y - 10, "taste.md", 12, "bold", "middle"))
        yl = mid_y
        for k, (x, y, hh, v) in Ln.items():
            hh2 = mid_h * v / max(1, sum(src_in.values()))
            out.append(R(x, y, nw, hh, scol[k], f"{k}: {100*v/max(1,tot_in):.0f}% of bullets ({v})", 0.95))
            out.append(f"<path d='M{x+nw:.0f},{y:.0f} C{(x+nw+Mx)/2:.0f},{y:.0f} {(x+nw+Mx)/2:.0f},{yl:.0f} {Mx-nw/2:.0f},{yl:.0f} L{Mx-nw/2:.0f},{yl+hh2:.0f} C{(x+nw+Mx)/2:.0f},{yl+hh2:.0f} {(x+nw+Mx)/2:.0f},{y+hh:.0f} {x+nw:.0f},{y+hh:.0f} Z' fill='{scol[k]}' opacity='0.35'><title>{esc(k)} → taste.md: {v} bullets</title></path>")
            out.append(T(x + nw + 8, y + hh / 2 + 4, f"{k} · {100*v/max(1,tot_in):.0f}% ({v})", 12, "600"))
            yl += hh2
        yr = mid_y
        for k, (x, y, hh, v) in Rn.items():
            hh2 = mid_h * v / max(1, sum(cons.values()))
            out.append(R(x, y, nw, hh, mcols.get(k, "var(--muted)"), f"{k}: {100*v/max(1,tot_out):.0f}% of consultations ({v})", 0.95))
            out.append(f"<path d='M{Mx+nw/2:.0f},{yr:.0f} C{(Mx+x)/2:.0f},{yr:.0f} {(Mx+x)/2:.0f},{y:.0f} {x:.0f},{y:.0f} L{x:.0f},{y+hh:.0f} C{(Mx+x)/2:.0f},{y+hh:.0f} {(Mx+x)/2:.0f},{yr+hh2:.0f} {Mx+nw/2:.0f},{yr+hh2:.0f} Z' fill='{mcols.get(k, "var(--muted)")}' opacity='0.35'><title>taste.md → {esc(k)}: {v} consultations</title></path>")
            out.append(T(x - 8, y + hh / 2 + 4, f"{k} · {100*v/max(1,tot_out):.0f}% ({v})", 12, "600", "end"))
            yr += hh2
        return "".join(out) + "</svg>"
    if src_in and cons: OV["sankey"] = sankey(src_in, cons); H.append(OV["sankey"])
    elif cons: OV["sankey"] = "<p class=muted>No bullets were learned in this range, so the taste-flow diagram has no input side. Widen the range to see it.</p>"; H.append(OV["sankey"])
    tw_pairs = []
    for m in ml:
        cr_ = 100 * written.get(m, 0) / max(1, pm[m]["asst"]); us_ = 100 * ma[m] / max(1, pm[m]["asst"]); tot_ = cr_ + us_
        if tot_ <= 0 or pm[m]["asst"] < 10: continue
        tw_pairs.append((m, {"creates": round(100 * cr_ / tot_), "uses": round(100 * us_ / tot_)}))
    tw_cols = {"creates": "var(--accent)", "uses": "var(--bar)"}
    OV["twoway"] = flex(stacked_h("Creates taste vs uses taste, by model", tw_pairs, list(tw_cols), tw_cols, "Share of the model's taste traffic", "Model", "Blue: bullets it created. Grey: bullets it used.", w=620, unit="%", total=False), legend(tw_cols, "Direction"))
    H.append(two(OV.get("twoway", ""),
                 hbars("Taste traffic by model", [(m, round(100 * (ma[m] + written.get(m, 0)) / max(1, pm[m]["asst"]), 1), "") for m in ml if pm[m]["asst"] >= 10], "Created + used, per 100 turns", "How much the model interacts with taste at all", ylabel="Model", lw=230, w=620)))
    # ---- chain of thought per model, and whether taste changes it ----
    h3("Chain of thought by model")
    H.append("<p class=charttip>Thinking = the visible reasoning text before a reply. Compares turns where the reasoning consulted taste with turns where it did not, for the same model, so the difference is what taste adds to (or removes from) the thinking.</p>")
    cot_rows = []; cot_ratio = []
    for m in ml:
        TT = [t for s_ in sessions for t in s_["turns"] if t["model"] == m]
        if len(TT) < 5: continue
        with_th = [t for t in TT if t["think"] > 0]; ct = [t for t in with_th if t["cited"]]; nt = [t for t in with_th if not t["cited"]]
        med_th = sorted(t["think"] for t in with_th)[len(with_th)//2] if with_th else 0
        mean = lambda xs, k: (sum(x[k] for x in xs) / len(xs)) if xs else 0
        th_c, th_n = mean(ct, "think"), mean(nt, "think"); out_c, out_n = mean(ct, "out"), mean(nt, "out")
        tools_c = (sum(len(t["tools"]) for t in ct) / len(ct)) if ct else 0; tools_n = (sum(len(t["tools"]) for t in nt) / len(nt)) if nt else 0
        share_out = 100 * (sum(t["think"] for t in TT) / 4) / max(1, sum(t["out"] for t in TT))
        ratio = (th_c / th_n) if (th_n and ct) else None
        cot_rows.append((m, len(TT), round(100 * len(with_th) / len(TT)), fmt(med_th), round(min(100, share_out)), fmt(round(th_c)) if ct else "—", fmt(round(th_n)) if nt else "—", (f"{100*(ratio-1):+.0f}%" if ratio else "—"), (f"{100*(out_c/out_n-1):+.0f}%" if (out_n and ct) else "—"), (f"{tools_c-tools_n:+.1f}" if (ct and nt) else "—")))
        if ratio: cot_ratio.append((m, round(ratio, 2), f"{fmt(round(th_c))} chars with taste vs {fmt(round(th_n))} without, over {len(ct)} and {len(nt)} thinking turns"))
    H.append(table(["Model", "#Turns", "#Turns with thinking %", "#Thinking chars per thinking turn (median)", "#Thinking as % of output", "#Thinking when taste used", "#Thinking when not", "#Taste effect on thinking", "#Taste effect on reply length", "#Extra tool calls per turn with taste"], cot_rows))
    H.append("<p class=charttip>Taste effect columns compare taste-consulting turns to the model's other turns <i>that also had thinking</i>, so turns with no reasoning at all do not skew the baseline. A positive thinking effect means the model reasons longer when it brings taste in; a negative one means taste shortcuts the reasoning. Extra tool calls above zero means taste-guided turns do more checking.</p>")
    if cot_ratio: H.append(two(hbars("Thinking length: taste turns ÷ other turns", cot_ratio, "Ratio (1.0 = no difference)", "Above 1 = taste makes the model think longer. Below 1 = taste shortens the reasoning.", ylabel="Model", lw=230, w=620),
                              hbars("Thinking as share of output", [(m, r[4], "") for m, r in zip([r[0] for r in cot_rows], cot_rows)], "Percent of output tokens that are reasoning (est.)", "Thinking chars ÷ 4 over output tokens", ylabel="Model", lw=230, w=620)))
    H.append(two(hbars("Taste activations per 100 turns, by model", [(m, round(100 * ma[m] / max(1, pm[m]["asst"]), 1), f"{ma[m]} activations over {pm[m]['asst']} turns") for m in ml], "Activations per 100 assistant turns", "How often each model consults the taste file", ylabel="Model", lw=230, w=620),
                 hbars("Thinking volume per turn, by model", [(m, round(pm[m]["think"] / max(1, pm[m]["asst"])), "") for m in ml], "Thinking characters per assistant turn", "Models that think more have more room to consult taste", ylabel="Model", lw=230, w=620)))
    weeks = collections.OrderedDict()
    for s in sessions:
        wk = (datetime.date.fromisoformat(s["date"]) - datetime.timedelta(days=datetime.date.fromisoformat(s["date"]).weekday())).isoformat()
        weeks.setdefault(wk, collections.Counter()).update(s["models"])
    H.append(two(tseries("Assistant turns per week by model", [(wk[5:], dict(c)) for wk, c in weeks.items()], ml, mcols, "Week starting", "Assistant turns", w=620, h=380, leg=legend(mcols, "Model")),
                 hbars("Cost by model", [(m, round(pm[m]["cost"], 2), "") for m in ml], "USD", "Logged by the provider; free tiers show 0", ylabel="Model", lw=230, w=620)))

    # ================= USAGE =================
    tab("Usage")
    allp = [t for s in sessions for _, t, *_x in s["prompts"] if t.strip()]
    lens = sorted(len(t) for t in allp) or [0]
    H.append(kpi([("sessions", len(sessions), ""), ("user prompts", len(allp), "Messages you typed, excluding tool results."), ("assistant turns", fmt(sum(s['asst'] for s in sessions)), "Model responses, including tool-calling steps. Many turns per prompt means long autonomous runs."), ("minutes", fmt(sum(s['minutes'] for s in sessions)), "Wall-clock time from first to last message per session."), ("cost per prompt", f"${tot_cost/max(1,len(allp)):.2f}", ""), ("median prompt chars", lens[len(lens)//2], "")]))
    # session timeline: x = date, y = assistant turns, bubble = minutes, color = model
    TW, TH, TL, TB, TR = 1240, 380, 70, 60, 30; mxt = max(s["asst"] for s in sessions) or 1
    ty = lambda v: TH - TB - v / mxt * (TH - TB - 60)
    HB = hour_buckets(); t0_utc = datetime.datetime.fromisoformat(HB[0][1]); t1_utc = datetime.datetime.fromisoformat(HB[-1][1]) + datetime.timedelta(hours=SIZE_H); tspan = max(1, (t1_utc - t0_utc).total_seconds())
    def tx(ts_):
        try: tt = datetime.datetime.fromisoformat(ts_.rstrip("Z"))
        except Exception: tt = t0_utc
        return TL + 20 + max(0.0, min(1.0, (tt - t0_utc).total_seconds() / tspan)) * (TW - TL - TR - 40)
    sub_t = f"Each bubble is a session at its first message. Height = assistant turns, size = minutes, color = model. Axis in local time, one tick per {BUCKET_WORD}."
    out = [svg_open(TW, TH), title_block(TW, "Session timeline", sub_t)]
    for v in range(0, mxt + 1, max(1, math.ceil(mxt / 5))): out.append(L(TL, ty(v), TW - TR, ty(v), "var(--grid)", 1)); out.append(T(TL - 8, ty(v) + 4, fmt(v), anchor="end"))
    step_ = max(1, math.ceil(len(HB) / 10))
    for i, (lbl, st_) in enumerate(HB):
        if i % step_: continue
        px = TL + 20 + i / len(HB) * (TW - TL - TR - 40); out.append(L(px, 50, px, TH - TB, "var(--grid)", 1)); out.append(T(px, TH - TB + 16, lbl, anchor="middle"))
    out.append(L(TL, 50, TL, TH - TB)); out.append(L(TL, TH - TB, TW - TR, TH - TB))
    out.append(T((TL + TW - TR) / 2, TH - 12, "Time (local)", 13, "bold", "middle")); out.append(T(16, (TH - TB + 50) / 2, "Assistant turns", 13, "bold", "middle", rot=True))
    for s in sorted(sessions, key=lambda s: -s["minutes"]):
        r = 4 + math.sqrt(max(1, s["minutes"])) * 0.55
        out.append(f"<circle cx='{tx(s['first']):.0f}' cy='{ty(s['asst']):.0f}' r='{r:.1f}' fill='{mcols.get(s['model'], 'var(--muted)')}' opacity='0.75' stroke='var(--bg)' stroke-width='1'><title>{esc(s['date'])} · {esc(s['title'] or s['sid'])}\n{esc(s['model'])}\n{s['user']} prompts · {s['asst']} turns · {s['minutes']} min · ${s['cost']:.2f} · {s['cites']} taste activations</title></circle>")
    OV["timeline"] = flex("".join(out) + "</svg>", legend(mcols, "Model"))
    H.append(OV["timeline"])
    weeks_u = collections.OrderedDict()
    for s in sessions:
        wk = (datetime.date.fromisoformat(s["date"]) - datetime.timedelta(days=datetime.date.fromisoformat(s["date"]).weekday())).isoformat()
        W_ = weeks_u.setdefault(wk, collections.Counter()); W_["prompts"] += s["user"]; W_["sessions"] += 1
    H.append("<details><summary>Sessions table</summary>" + table(["Date", "Session", "Model", "#Prompts", "#Turns", "#Turns/prompt", "#Minutes", "#Tool calls", "#Cost $", "#Activations", "#Pushbacks"], [(s["date"], f"{s['sid']} {s['title']}", s["model"][:26], s["user"], s["asst"], round(s["asst"] / max(1, s["user"]), 1), s["minutes"], sum(s["tools"].values()), round(s["cost"], 2), s["cites"], s["push"]) for s in sessions]) + "</details>")
    tools = collections.Counter()
    for s in sessions: tools.update(s["tools"])
    ed = tools.get("edit_file", 0) + tools.get("write_file", 0); rd = tools.get("read_file", 0) + tools.get("grep", 0) + tools.get("glob", 0) + tools.get("read_multiple_files", 0)
    tools_chart = hbars("Tool calls", [(t, k, "") for t, k in tools.most_common(15)], "Calls", f"Edits per read {ed/max(1,rd):.2f} · shell calls per edit {tools.get('shell_command',0)/max(1,ed):.1f} · subagents {tools.get('agent',0)}", ylabel="Tool", w=620, lw=170)
    hours = collections.defaultdict(collections.Counter); wdays = collections.defaultdict(collections.Counter); opens = collections.defaultdict(collections.Counter); plens = collections.defaultdict(collections.Counter)
    slash = 0
    for s in sessions:
        mdl_s = s["model"]
        for ts, t, *_x in s["prompts"]:
            if not t.strip(): continue
            try: dt = datetime.datetime.fromisoformat(ts.rstrip("Z")); hours[dt.hour][mdl_s] += 1; wdays[dt.strftime("%a")][mdl_s] += 1
            except Exception: pass
            if t.lstrip().startswith("/"): slash += 1
            else: opens[" ".join(re.findall(r"[a-z']+", t.lower())[:2])][mdl_s] += 1
            L_ = len(t); plens[("1000+" if L_ >= 1000 else f"{L_//100*100}–{L_//100*100+99}")][mdl_s] += 1
    mleg = legend(mcols, "Model")
    hour_chart = flex(stacked_h("Prompts by time of day (UTC)", [(f"{h:02d}:00", dict(hours[h])) for h in range(24) if hours.get(h)], ml, mcols, "Prompts", "Hour", "Stacked by the model the session was running", w=620), mleg)
    wday_chart = flex(stacked_h("Prompts by weekday", [(w, dict(wdays[w])) for w in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"] if wdays.get(w)], ml, mcols, "Prompts", "Day", "Stacked by model", w=620), mleg)
    top_open = sorted(opens, key=lambda o: -sum(opens[o].values()))[:12]
    open_chart = flex(stacked_h("How prompts open (first two words)", [(o, dict(opens[o])) for o in top_open], ml, mcols, "Prompts", "Opening", f"{slash} prompts were slash commands · stacked by model", w=620), mleg)
    len_keys = sorted(plens, key=lambda k: 10**6 if k == "1000+" else int(k.split("–")[0]))
    len_chart = flex(stacked_h("Prompt length", [(k, dict(plens[k])) for k in len_keys], ml, mcols, "Prompts", "Characters", "Stacked by model", w=620), mleg)
    ph = by_hour([dict(_s=s_["model"], ts=p[0]) for s_ in sessions for p in s_["prompts"] if p[1].strip()], lambda a: a["ts"][:19])
    act_chart = tseries(f"Prompts {PER} (local time)", [(l, dict(c)) for l, c in ph.items()], ml, mcols, BUCKET_WORD.capitalize(), "Prompts", w=620, h=300, leg=legend(mcols, "Model"))
    H.append(two(act_chart, tools_chart)); H.append(two(open_chart, len_chart)); H.append(two(hour_chart, wday_chart))

    # ================= HEALTH =================
    tab("Health")
    long_b = [r for r in rows if r["n"] > 300]
    def jac(a, b): return len(a & b) / max(1, len(a | b))
    dups = [(i, j) for i in range(len(rows)) for j in range(i + 1, len(rows)) if jac(btoks[rows[i]["i"]-1], btoks[rows[j]["i"]-1]) >= 0.6]
    cc_hist = collections.Counter(round(r["conf"], 2) for r in rows)
    H.append(kpi([("bytes", fmt(len(raw)), ""), ("over 300 chars", len(long_b), "Long bullets read like incident reports rather than preferences and cost tokens every turn."), ("near-duplicates", len(dups), "Pairs of bullets sharing most of their words."), ("never activated", len(never), "Bullets never referenced in any recorded reasoning."), ("learn events in cmd", len(learn_events), "Times the agent called the taste tool inside a cmd session. Most bullets came from mining other agents' sessions instead.")]))
    size_chart = hbars("Base prompt size per session", [(f"{s['date']} {s['title'] or s['sid']}", s["first_in"] or 0, "") for s in sessions], "First-turn input tokens", "Growth over time is mostly the taste file", ylabel="Session", lw=330, w=1240, sort=False)
    len_bins = [(f"{lo}–{lo+99}", sum(1 for r in rows if lo <= r["n"] < lo + 100), "") for lo in range(0, 700, 100)] + [("700+", sum(1 for r in rows if r["n"] >= 700), "")]
    blen_chart = hbars("Bullet length", len_bins, "Number of bullets", "Characters per bullet. Long bullets read like incident reports and cost tokens every turn.", ylabel="Characters", w=620, lw=110, sort=False)
    byday = collections.Counter(r["date"] for r in rows); W2, H2 = 1240, 190; GX0, GX1 = 70, W2 - 30
    out = [svg_open(W2, H2), title_block(W2, "Bullets added over time")]
    acc = 0; pts = []
    for dd in sorted(byday):
        acc += byday[dd]; px = GX0 + (datetime.date.fromisoformat(dd) - d0).days / span * (GX1 - GX0); py = H2 - 30 - acc / len(rows) * (H2 - 70); pts.append(f"{px:.0f},{py:.0f}")
        out.append(R(px - 2, H2 - 30 - byday[dd] * 4, 4, byday[dd] * 4, "var(--muted)", f"{dd}: {byday[dd]} bullets"))
    out.append(f"<polyline points='{GX0},{H2-30} {' '.join(pts)}' fill='none' stroke='var(--fg)' stroke-width='2'/>")
    out.append(T(GX0, H2 - 8, d0)); out.append(T(GX1, H2 - 8, d1, anchor="end")); out.append(T(GX0 - 8, 44, len(rows), anchor="end")); out.append(T(GX0 - 8, H2 - 30, 0, anchor="end"))
    growth_chart = "".join(out) + "</svg>"
    conf_chart = hbars("Confidence distribution", [(f"{c:.2f}", k, "") for c, k in sorted(cc_hist.items(), reverse=True)], "Number of bullets", ylabel="Confidence", w=620, lw=90, sort=False)
    H.append(two(conf_chart, blen_chart)); H.append(size_chart); H.append(growth_chart); OV["size"] = size_chart
    h3("Never-activated bullets"); H.append("<p class=muted>Present in every prompt, never referenced in any recorded thinking. Candidates to trim.</p><details><summary>Show " + str(len(never)) + "</summary><ul>" + "".join(f"<li><span class=muted>#{r['i']} · {r['conf']:.2f}</span> {esc(r['text'][:200])}</li>" for r in never) + "</ul></details>")
    h3("Longest bullets"); H.append("<details><summary>Show " + str(len(long_b)) + " over 300 chars</summary><ul>" + "".join(f"<li><span class=muted>#{r['i']} · {r['n']} chars</span> {esc(r['text'][:160])}…</li>" for r in sorted(long_b, key=lambda r: -r["n"])) + "</ul></details>")
    if dups: h3("Near-duplicates"); H.append("<ul>" + "".join(f"<li>#{rows[i]['i']} ≈ #{rows[j]['i']}: {esc(rows[i]['text'][:100])}…</li>" for i, j in dups) + "</ul>")
    H.append("</div>")
    ov = [f"<div class=tab id='tab-Overview{SUF}'><p class=tabdesc>Cost, speed and taste at a glance.</p>"]
    ov.append(kpi([("logged cost", f"${tot_cost:,.2f}", "What the provider billed in this range. Free tiers show $0."), ("input tokens", fmt(tot_in), "Everything sent to the model, every turn, taste file included."), ("cache hit", f"{100*tot_cr/max(1,tot_in):.0f}%", "Share of input served from the prompt cache instead of re-billed."), ("output tok/s", round(med([v for _, v in tps_samples]), 1), "Wall-clock output speed you experienced, median turn."), ("taste share of prompt", f"{100*est_tokens/max(1, latest_in):.0f}%", "How much of each request the taste file occupies."), ("taste use per 100 turns", round(100 * len(acts) / max(1, sum(s['asst'] for s in sessions)), 1), "How often the model's reasoning consulted a taste bullet."), ("steering share", f"{100*n_steer/max(1,len(acts)):.0f}%", "Of those consultations, how often the plan changed."), ("unused bullets", len(never), "Bullets injected into every prompt but never consulted in this range.")]))
    ins = []
    if ml:
        top_cost = max(ml, key=lambda m: pm[m]["cost"]); tc_ = pm[top_cost]["cost"]
        if tot_cost > 0: ins.append(f"<b>{esc(top_cost)}</b> accounts for {100*tc_/tot_cost:.0f}% of logged cost with {100*pm[top_cost]['asst']/max(1,sum(s['asst'] for s in sessions)):.0f}% of turns.")
        rate = {m: 100 * ma[m] / max(1, pm[m]["asst"]) for m in ml if pm[m]["asst"] >= 20}
        if len(rate) >= 2:
            hi_m = max(rate, key=rate.get); lo_m = min(rate, key=rate.get)
            ins.append(f"Taste is consulted most by <b>{esc(hi_m)}</b> ({rate[hi_m]:.0f} per 100 turns) and least by <b>{esc(lo_m)}</b> ({rate[lo_m]:.1f}).")
        wr = {m: 100 * written.get(m, 0) / max(1, pm[m]["asst"]) for m in ml if pm[m]["asst"] >= 20}
        if wr and max(wr.values()) > 0:
            w_m = max(wr, key=wr.get)
            ins.append(f"<b>{esc(w_m)}</b> sessions produce the most new bullets ({wr[w_m]:.1f} per 100 turns); overall the loop runs about {len(acts)/max(1,sum(written.values())):.0f} consultations per bullet written.")
        fast = max((m for m in ml if tps_med.get(m)), key=lambda m: tps_med[m], default=None)
        if fast: ins.append(f"Fastest model: <b>{esc(fast)}</b> at {tps_med[fast]:.0f} output tok/s (median turn).")
    if cot_rows:
        eff = {r[0]: r[7] for r in cot_rows if isinstance(r[7], str) and r[7].endswith("%")}
        effv = {m: int(v.rstrip("%")) for m, v in eff.items()}
        if effv:
            strong = [m for m, v in effv.items() if v >= 50]; weak = [m for m, v in effv.items() if v < 20]
            lo_eff, hi_eff = min(effv.values()), max(effv.values())
            if strong: ins.append(f"Taste is not a shortcut: on {len(strong)} of {len(effv)} models a taste-consulting turn carries {min(effv[m] for m in strong)//100+1}× to {max(effv[m] for m in strong)//100+1}× the reasoning of that model's other thinking turns. Caveat: taste tends to be consulted on harder, planning-type turns, which would run longer anyway.")
            for m in weak:
                ss_ = round(100 * ms[m] / max(1, ma[m])) if ma[m] else 0
                ins.append(f"<b>{esc(m)}</b> mentions taste without reasoning about it: thinking barely changes ({eff[m]}) and only {ss_}% of its consultations change the plan.")
    ins.append(f"{len(never)} of {len(rows)} bullets ({100*len(never)/max(1,len(rows)):.0f}%) were never consulted in this range yet ride along in every prompt.")
    if ago_min is not None: ins.insert(0, f"Last activity <b>{ago_min} min ago</b>" + (f" ({esc(last_local.strftime('%H:%M'))} local)" if last_local else "") + ". Re-run the script to refresh.")
    cur[0] = "Overview"
    ov.append("<div class=card><b>Taste</b> is the file of learned preferences cmd pastes into every prompt. An <b>activation</b> is a moment the model's reasoning consulted one bullet; <b>steering</b> means it then changed the plan. Hover any <span class=tip>?</span> for a definition. Counts are keyword-matched and approximate.</div>")
    ov.append("<div class=card><b>Insights</b><ul style='margin:6px 0 0'>" + "".join(f"<li>{x}</li>" for x in ins) + "</ul></div>")
    h3("Taste flow", ov); ov.append(OV.get("sankey", "")); ov.append(two(OV.get("twoway", ""), OV.get("work", "")))
    h3("Activity", ov)
    th_ = by_hour([dict(_s=t["model"], ts=t["ts"]) for s_ in sessions for t in s_["turns"]], lambda a: a["ts"][:19])
    ch_ = by_hour([dict(_s=t["model"], _v=t["cost"], ts=t["ts"]) for s_ in sessions for t in s_["turns"]], lambda a: a["ts"][:19])
    TOKC = {"cached input": "var(--bar2)", "uncached input": "var(--accent)", "output": "#3ecf8e"}; TOKS = list(TOKC)
    def tok_items(src):
        return [dict(_s="cached input", _v=t["cr"], ts=t["ts"]) for s_ in src for t in s_["turns"]] + [dict(_s="uncached input", _v=max(0, t["inp"] - t["cr"]), ts=t["ts"]) for s_ in src for t in s_["turns"]] + [dict(_s="output", _v=t["out"], ts=t["ts"]) for s_ in src for t in s_["turns"]]
    tk_ = by_hour(tok_items(sessions), lambda a: a["ts"][:19])
    cost_pane = tseries(f"Cost {PER} (local time)", [(l, {m: round(v, 3) for m, v in c.items()}) for l, c in ch_.items()], ml, mcols, BUCKET_WORD.capitalize(), "USD", "Stacked by model; free tiers add nothing", w=620, h=320, leg=legend(mcols, "Model"))
    tok_pane = tseries(f"Tokens {PER} (local time)", [(l, dict(c)) for l, c in tk_.items()], TOKS, TOKC, BUCKET_WORD.capitalize(), "Tokens", "Cached input is re-read from the prompt cache and billed at a fraction of uncached", w=620, h=320, leg=legend(TOKC, "Token kind"))
    ov.append(two(tseries(f"Assistant turns {PER} (local time)", [(l, dict(c)) for l, c in th_.items()], ml, mcols, BUCKET_WORD.capitalize(), "Turns", w=620, h=320, leg=legend(mcols, "Model")),
                  toggle([("Cost", cost_pane), ("Tokens", tok_pane)])))
    if gran != "hour":
        # keep the live feel: last 24 hours by hour, from the unclipped sessions
        th24 = by_hour([dict(_s=t["model"], ts=t["ts"]) for s_ in ALL_SESSIONS for t in s_["turns"]], lambda a: a["ts"][:19], size_h=1, n=24)
        ch24 = by_hour([dict(_s=t["model"], _v=t["cost"], ts=t["ts"]) for s_ in ALL_SESSIONS for t in s_["turns"]], lambda a: a["ts"][:19], size_h=1, n=24)
        if any(sum(c.values()) for c in th24.values()):
            tk24 = by_hour(tok_items(ALL_SESSIONS), lambda a: a["ts"][:19], size_h=1, n=24)
            ov.append(two(tseries("Last 24 hours: assistant turns per hour", [(l, dict(c)) for l, c in th24.items()], [m for m in ALL_MODELS], ALL_MCOLS, "Hour", "Turns", w=620, h=300, leg=legend(ALL_MCOLS, "Model")),
                          toggle([("Cost", tseries("Last 24 hours: cost per hour", [(l, {m: round(v, 3) for m, v in c.items()}) for l, c in ch24.items()], [m for m in ALL_MODELS], ALL_MCOLS, "Hour", "USD", "Stacked by model", w=620, h=300, leg=legend(ALL_MCOLS, "Model"))),
                                  ("Tokens", tseries("Last 24 hours: tokens per hour", [(l, dict(c)) for l, c in tk24.items()], TOKS, TOKC, "Hour", "Tokens", "Cached vs uncached input, plus output", w=620, h=300, leg=legend(TOKC, "Token kind")))])))
    h3("Cost and speed", ov)
    ov.append("" if True else "<div class=card><b>Taste</b> is the file of learned preferences cmd pastes into every prompt. An <b>activation</b> is a moment the model's reasoning consulted one bullet; <b>steering</b> means it then changed the plan. Hover any <span class=tip>?</span> for a definition. Counts are keyword-matched and approximate.</div>")
    ov.append(two(OV.get("cost", ""), OV.get("speed", "")))
    h3("Habits", ov); ov.append(two(OV.get("infl", ""), OV.get("habits", "")))
    h3("Sessions", ov); ov.append(OV.get("timeline", ""))
    ov.append("</div>")
    H[0:0] = ov
    subnav = "".join(f"<div class=subnav data-for='{n}{SUF}'>" + "".join(f"<a href='#{i}'>{esc(t)}</a>" for i, t in SECS.get(n, [])) + "</div>" for n, _ in TABS)
    H.insert(0, "<div class=tabs><div class=tabrow><div class=tabbtns>" + "".join(f"<button data-tab='{n}{SUF}'>{n}</button>" for n, _ in TABS) + "</div>" + CTL + "</div>" + subnav + "</div>")

    return H

NOW = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
def _cut_ts(hours): return (NOW - datetime.timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S.000Z") if hours else "0000"
RANGES = [("24h", "Today", 24), ("7d", "7 days", 168), ("30d", "30 days", 720), ("all", "All time", None)]
views = []
for k, lab, hours in RANGES:
    c = _cut_ts(hours)
    sv = [x for x in (clip_session(s_, c) for s_ in sessions) if x] if hours else sessions
    views.append((k, lab, rows, sv, [a for a in acts if a["ts"] >= c], c, {"24h": "hour", "7d": "6h", "30d": "day", "all": "week"}[k]))
DEFAULT_VIEW = next((k for k, _, _, sv, *_ in views if sv), "all")
H.append("<div class=topbar><div><h1>Command Code dashboard</h1><p class=sub>" + ("Public build, names redacted · " if PUBLIC else "") + f"Rendered {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')} local · re-run the script to refresh · <code>{esc(redact(TASTE))}</code></p></div>")
CTL = "<div class=ctl><div class='seg toggle'>" + "".join(f"<button data-v='{k}'>{lab}</button>" for k, lab, *_ in views) + "</div><div class='seg theme'><button data-th='dark'>Dark</button><button data-th='light'>Light</button></div></div>"
H.append("</div>")
for k, lab, rv, sv, av, cut_, gran_ in views:
    H.append(f"<div class=view id='view-{k}'" + (" style='display:none'" if k != DEFAULT_VIEW else "") + ">")
    if rv and sv: H += build("-" + k, rv, sv, av, [r for r in rows if r["date"] >= cut_[:10]], gran=gran_, cut=cut_)
    else: H.append(f"<p class=muted style='padding:24px 0'>No cmd sessions in this range. Pick a wider range above.</p>")
    H.append("</div>")
H.append("<script>document.querySelectorAll('.tabs').forEach(bar=>{const bs=[...bar.querySelectorAll('.tabbtns button')];bs.forEach((b,i)=>{b.onclick=()=>{bs.forEach(x=>x.classList.remove('on'));b.classList.add('on');const v=bar.parentElement;v.querySelectorAll('.tab').forEach(t=>{t.classList.remove('on','anim')});const tt=v.querySelector('#tab-'+b.dataset.tab);tt.classList.add('on');if(window.__painted){tt.classList.add('anim');}bar.querySelectorAll('.subnav').forEach(sn=>sn.classList.toggle('on',sn.dataset.for===b.dataset.tab));window.cmdtab=i;};});bs[0].click();});</script>")
H.append("<script>const showRange=k=>{const b=document.querySelector(`.toggle button[data-v='${k}']`);if(!b)return;document.querySelectorAll('.view').forEach(v=>v.style.display='none');const v=document.getElementById('view-'+k);v.style.display='';document.querySelectorAll('.toggle button').forEach(x=>x.classList.toggle('on',x.dataset.v===k));localStorage.setItem('cmdrange',k);const i=window.cmdtab||0;const tb=v.querySelectorAll('.tabbtns button')[i];if(tb)tb.click();};document.querySelectorAll('.toggle button').forEach(b=>b.onclick=()=>showRange(b.dataset.v));const hp=new URLSearchParams(location.hash.slice(1));const ht=hp.get('tab');if(ht){const names=[...document.querySelectorAll('.tabs')][0].querySelectorAll('button');const idx=[...names].findIndex(x=>x.textContent.trim().toLowerCase()===ht.toLowerCase());if(idx>=0)window.cmdtab=idx;}showRange(hp.get('range')||'" + DEFAULT_VIEW + "');"
         "const setTh=t=>{document.documentElement.dataset.theme=t;localStorage.setItem('cmdtheme',t);document.querySelectorAll('.theme button').forEach(x=>x.classList.toggle('on',x.dataset.th===t));};document.querySelectorAll('.theme button').forEach(b=>b.onclick=()=>setTh(b.dataset.th));setTh(localStorage.getItem('cmdtheme')||'dark');</script>")
H.append("<div id=tt class=tt></div>")
H.append("<script>(function(){const tt=document.getElementById('tt');let cur=null;document.addEventListener('mousemove',e=>{const r=e.target.closest('rect.hit');if(!r){if(cur){cur=null;tt.style.opacity=0;}return;}if(r!==cur){cur=r;tt.innerHTML=r.dataset.tip;tt.style.opacity=1;}const x=e.clientX+14,y=e.clientY+14;const bw=tt.offsetWidth,bh=tt.offsetHeight;tt.style.left=(x+bw>innerWidth-8?e.clientX-bw-14:x)+'px';tt.style.top=(y+bh>innerHeight-8?e.clientY-bh-14:y)+'px';});})();</script>")
H.append("<script>(function(){if(matchMedia('(prefers-reduced-motion: reduce)').matches||location.hash.includes('nomotion')){document.querySelectorAll('.reveal').forEach(el=>el.classList.add('seen'));return;}const io=new IntersectionObserver(es=>{es.forEach(en=>{if(en.isIntersecting){en.target.classList.add('seen');io.unobserve(en.target);}});},{rootMargin:'0px 0px -8% 0px',threshold:0.08});document.querySelectorAll('.chart,.kpi,.card,table,.legend').forEach(el=>{el.classList.add('reveal');io.observe(el);});setTimeout(()=>document.querySelectorAll('.reveal:not(.seen)').forEach(el=>{const r=el.getBoundingClientRect();if(r.top<innerHeight*1.2)el.classList.add('seen');}),1200);})();</script>")
H.append("<script>requestAnimationFrame(()=>requestAnimationFrame(()=>{window.__painted=true;}));</script>")
H.append("<script>document.addEventListener('click',e=>{const b=e.target.closest('.seg.mini button');if(!b)return;const w=b.closest('.tgwrap'),id=b.parentElement.dataset.tg;w.querySelectorAll(`.seg.mini[data-tg='${id}'] button`).forEach(x=>x.classList.toggle('on',x===b));w.querySelectorAll(`.tgpane[data-tg='${id}']`).forEach(p=>p.style.display=p.dataset.i===b.dataset.i?'':'none');});</script>")
H.append("<script>document.addEventListener('click',e=>{const th=e.target.closest('table.sortable th');if(!th)return;const tbl=th.closest('table'),i=[...th.parentNode.children].indexOf(th),rows=[...tbl.querySelectorAll('tr')].slice(1),tb=rows[0]&&rows[0].parentNode;if(!tb)return;if(!tbl._orig)tbl._orig=rows.slice();const state=th.classList.contains('desc')?'asc':th.classList.contains('asc')?'reset':'desc';tbl.querySelectorAll('th').forEach(x=>x.classList.remove('asc','desc'));tbl.classList.remove('sorted');tbl.querySelectorAll('td').forEach(td=>{td.style.background='';td.classList.remove('sortcol')});if(state==='reset'){tbl._orig.forEach(r=>tb.appendChild(r));return;}th.classList.add(state);tbl.classList.add('sorted');const asc=state==='asc';const val=r=>{const s=r.children[i].textContent.trim().replace(/[$,%]/g,'');const m=s.match(/^(-?[\\d.]+)\\s*([kM])?$/);return m?parseFloat(m[1])*(m[2]==='k'?1e3:m[2]==='M'?1e6:1):s.toLowerCase()};rows.slice().sort((a,b)=>{const x=val(a),y=val(b);return (typeof x==='number'&&typeof y==='number')?(asc?x-y:y-x):(asc?String(x).localeCompare(String(y)):String(y).localeCompare(String(x)))}).forEach(r=>tb.appendChild(r));const vals=rows.map(val);const nums=vals.filter(v=>typeof v==='number');const lo=Math.min(...nums),hi=Math.max(...nums);rows.forEach((r,j)=>{const td=r.children[i];if(!td)return;td.classList.add('sortcol');if(typeof vals[j]==='number'&&hi>lo)td.style.background=`color-mix(in srgb, var(--accent) ${Math.round(6+44*(vals[j]-lo)/(hi-lo))}%, transparent)`;});});</script>")
open(OUT, "w").write("\n".join(H))
print("wrote", OUT, "| bullets", len(rows), "| sessions", len(sessions), "| activations", len(acts), "steering", sum(1 for a in acts if a["steer"]))
if not A.no_open:
    try: subprocess.Popen(["xdg-open", f"file://{OUT}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception: pass
