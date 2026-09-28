"""
Persistent token-usage ledger for Supervertaler Workbench.

Writes one metadata-only JSON line per AI call to a monthly file under
``<user_data>/workbench/usage/usage-YYYY-MM.jsonl`` — the SAME schema as the
Supervertaler for Trados plugin, so the two products' logs are interchangeable.

Records metadata only (model, token counts, cost, project, file, language pair)
— never the prompt or response text. Best-effort: any failure is swallowed so
logging never disrupts a translation.

Cost is computed from the canonical pricing.json via ``modules.llm_pricing``.
"""

import csv
import datetime
import hashlib
import json
import os
import threading
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from modules.llm_pricing import compute_actual_cost

# ── Module configuration (set once at app start) ─────────────────────────────
_config = {"root": None, "version": "", "enabled": True}


def configure(root_dir, app_version: str = "", enabled: bool = True) -> None:
    """Point the logger at the user-data root and record the app version."""
    _config["root"] = str(root_dir) if root_dir else None
    _config["version"] = app_version or ""
    _config["enabled"] = bool(enabled)


def set_enabled(enabled: bool) -> None:
    _config["enabled"] = bool(enabled)


def usage_dir() -> Optional[Path]:
    if not _config["root"]:
        return None
    return Path(_config["root"]) / "workbench" / "usage"


def _month_file(ts: datetime.datetime) -> Optional[Path]:
    d = usage_dir()
    if d is None:
        return None
    return d / f"usage-{ts:%Y-%m}.jsonl"


# ── Ambient call context (thread-local) ──────────────────────────────────────
_ctx = threading.local()


def _ctx_get(name: str):
    return getattr(_ctx, name, None)


class UsageContext:
    """Context manager attaching task/project/file/language attribution to any
    AI calls made within it. Use around batch translation, etc."""

    def __init__(self, task=None, project=None, file=None, src_lang=None,
                 tgt_lang=None, client=None):
        self._new = dict(task=task, project=project, file=file,
                         src_lang=src_lang, tgt_lang=tgt_lang, client=client)
        self._old = {}

    def __enter__(self):
        for k, v in self._new.items():
            self._old[k] = getattr(_ctx, k, None)
            setattr(_ctx, k, v)
        return self

    def __exit__(self, *exc):
        for k, v in self._old.items():
            setattr(_ctx, k, v)
        return False


def set_context(task=None, project=None, file=None, src_lang=None,
                tgt_lang=None, client=None) -> None:
    """Set the thread-local attribution for subsequent AI calls on this thread.
    Used by the batch worker thread (which is short-lived, so an explicit clear
    is optional). Prefer :class:`UsageContext` on the main thread."""
    _ctx.task = task
    _ctx.project = project
    _ctx.file = file
    _ctx.src_lang = src_lang
    _ctx.tgt_lang = tgt_lang
    _ctx.client = client


def clear_context() -> None:
    for k in ("task", "project", "file", "src_lang", "tgt_lang", "client"):
        setattr(_ctx, k, None)


# Fallback attribution for calls made outside an explicit context (Ctrl+T,
# chat, AutoTagger …): a callable returning e.g. {"project": ..., "src_lang":
# ..., "tgt_lang": ...} for whatever is open right now, so those calls count
# towards the open project in the per-project report too (issue #8).
_default_ctx = {"fn": None}


def set_default_context(fn) -> None:
    _default_ctx["fn"] = fn


def _default_context() -> Dict:
    fn = _default_ctx["fn"]
    if fn is None or _ctx_get("project"):
        return {}
    try:
        return fn() or {}
    except Exception:
        return {}


def _project_key(project: Optional[str]) -> str:
    if not project:
        return ""
    return hashlib.sha256(project.encode("utf-8")).hexdigest()[:12]


# ── Recording ────────────────────────────────────────────────────────────────
def record(provider: str, model: str, usage: Optional[Dict],
           estimated_input: int = 0, estimated_output: int = 0,
           duration_s: float = 0.0, ok: bool = True,
           error: Optional[str] = None, task: Optional[str] = None) -> None:
    """Append one usage record. ``usage`` is the provider-reported dict from
    ``translate_with_usage`` (keys input_tokens/output_tokens/cache_*); when it
    is empty/None, the chars/4 estimates are used and the record is flagged
    ``estimated``."""
    try:
        if not _config["enabled"] or _config["root"] is None:
            return

        has_actual = bool(usage) and (usage.get("input_tokens") or usage.get("output_tokens"))
        if has_actual:
            in_total = int(usage.get("input_tokens") or 0)
            cache_read = int(usage.get("cache_read_input_tokens") or 0)
            cache_write = int(usage.get("cache_creation_input_tokens") or 0)
            # input_tokens from the providers is the TOTAL input (regular + cached).
            in_regular = max(0, in_total - cache_read - cache_write)
            out_tokens = int(usage.get("output_tokens") or 0)
            source = "actual"
        else:
            in_regular = int(estimated_input or 0)
            cache_read = 0
            cache_write = 0
            out_tokens = int(estimated_output or 0)
            source = "estimated"

        # Cache-aware cost: cached input is billed below the full input rate
        # (per-provider multipliers), matching the Trados plugin exactly. For the
        # estimated path (no provider usage), cache_read/cache_write are 0 so this
        # reduces to the plain input-rate calculation.
        cost = compute_actual_cost(provider, model, in_regular, cache_read, cache_write, out_tokens)
        cost_known = cost is not None

        fallback = _default_context()

        def ctx(name):
            return _ctx_get(name) or fallback.get(name)

        project = ctx("project")
        ts = datetime.datetime.now(datetime.timezone.utc)
        rec = {
            "id": uuid.uuid4().hex,
            "ts": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "product": "workbench",
            "app_version": _config["version"],
            "task": task or _ctx_get("task") or "Translate",
            "provider": provider or "",
            "model": model or "",
            "project": project or "",
            "project_key": _project_key(project),
            "file": ctx("file") or "",
            "client": ctx("client") or "",
            "src_lang": ctx("src_lang") or "",
            "tgt_lang": ctx("tgt_lang") or "",
            "in_regular": in_regular,
            "in_cache_read": cache_read,
            "in_cache_write": cache_write,
            "out": out_tokens,
            "source": source,
            "cost_usd": round(cost, 6) if cost is not None else 0.0,
            "cost_known": cost_known,
            "duration_s": round(float(duration_s or 0.0), 3),
            "ok": bool(ok),
            "error": error,
        }

        path = _month_file(ts)
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass  # never let logging disrupt a translation


# ── Reading / aggregation ────────────────────────────────────────────────────
def load(from_utc: datetime.datetime, to_utc: datetime.datetime) -> List[Dict]:
    """All records with a timestamp in [from_utc, to_utc]."""
    out: List[Dict] = []
    d = usage_dir()
    if d is None or not d.is_dir():
        return out
    for path in sorted(d.glob("usage-*.jsonl")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    ts = _parse_ts(rec.get("ts"))
                    if ts is not None and (ts < from_utc or ts > to_utc):
                        continue
                    out.append(rec)
        except Exception:
            continue
    return out


def _parse_ts(ts: Optional[str]) -> Optional[datetime.datetime]:
    if not ts:
        return None
    try:
        return datetime.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=datetime.timezone.utc)
    except Exception:
        return None


_DIMENSIONS = {
    "Project": lambda r: r.get("project") or "(none)",
    "Client": lambda r: r.get("client") or "(none)",
    "Model": lambda r: r.get("model") or "(none)",
    "Provider": lambda r: r.get("provider") or "(none)",
    "Task": lambda r: r.get("task") or "(none)",
    "Day": lambda r: (r.get("ts") or "")[:10] or "(unknown)",
    "Month": lambda r: (r.get("ts") or "")[:7] or "(unknown)",
}

DIMENSIONS = list(_DIMENSIONS.keys())


def _row_tokens(r: Dict) -> Tuple[int, int]:
    in_tok = int(r.get("in_regular") or 0) + int(r.get("in_cache_read") or 0) + int(r.get("in_cache_write") or 0)
    return in_tok, int(r.get("out") or 0)


def group(records: List[Dict], dimension: str) -> List[Dict]:
    """Aggregate records into report rows for the given dimension, cost-desc."""
    keyfn = _DIMENSIONS.get(dimension, _DIMENSIONS["Project"])
    rows: Dict[str, Dict] = {}
    for r in records:
        k = keyfn(r)
        row = rows.get(k)
        if row is None:
            row = {"group": k, "calls": 0, "input": 0, "output": 0, "cost_usd": 0.0, "actual": 0}
            rows[k] = row
        in_tok, out_tok = _row_tokens(r)
        row["calls"] += 1
        row["input"] += in_tok
        row["output"] += out_tok
        row["cost_usd"] += float(r.get("cost_usd") or 0.0)
        if (r.get("source") or "") == "actual":
            row["actual"] += 1
    result = list(rows.values())
    for row in result:
        row["total"] = row["input"] + row["output"]
        row["actual_pct"] = (100 * row["actual"] // row["calls"]) if row["calls"] else 0
    result.sort(key=lambda x: (x["cost_usd"], x["total"]), reverse=True)
    return result


def totals(records: List[Dict]) -> Dict:
    t = {"group": "TOTAL", "calls": 0, "input": 0, "output": 0, "cost_usd": 0.0, "actual": 0}
    for r in records:
        in_tok, out_tok = _row_tokens(r)
        t["calls"] += 1
        t["input"] += in_tok
        t["output"] += out_tok
        t["cost_usd"] += float(r.get("cost_usd") or 0.0)
        if (r.get("source") or "") == "actual":
            t["actual"] += 1
    t["total"] = t["input"] + t["output"]
    t["actual_pct"] = (100 * t["actual"] // t["calls"]) if t["calls"] else 0
    return t


def project_totals(project: str) -> Dict:
    """All-time totals for one project (the per-job cost, issue #8)."""
    everything = load(datetime.datetime(2000, 1, 1, tzinfo=datetime.timezone.utc),
                      datetime.datetime(2999, 1, 1, tzinfo=datetime.timezone.utc))
    t = totals([r for r in everything if project and (r.get("project") or "") == project])
    t["unpriced"] = sum(1 for r in everything
                        if project and (r.get("project") or "") == project
                        and r.get("cost_known") is False)
    return t


# ── Budget ───────────────────────────────────────────────────────────────────
def month_to_date_cost() -> float:
    now = datetime.datetime.now(datetime.timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return round(sum(float(r.get("cost_usd") or 0.0) for r in load(start, now)), 6)


# ── Export ───────────────────────────────────────────────────────────────────
_EXPORT_COLUMNS = [
    ("timestamp_utc", "ts"), ("product", "product"), ("app_version", "app_version"),
    ("task", "task"), ("provider", "provider"), ("model", "model"),
    ("project", "project"), ("project_key", "project_key"), ("file", "file"),
    ("client", "client"), ("src_lang", "src_lang"), ("tgt_lang", "tgt_lang"),
    ("input_regular", "in_regular"), ("input_cache_read", "in_cache_read"),
    ("input_cache_write", "in_cache_write"), ("output", "out"),
    ("source", "source"), ("cost_usd", "cost_usd"), ("cost_known", "cost_known"),
    ("duration_s", "duration_s"), ("ok", "ok"), ("error", "error"),
]
_NUMERIC_KEYS = {"in_regular", "in_cache_read", "in_cache_write", "out", "cost_usd", "duration_s"}


def export_csv(path: str, records: List[Dict]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow([h for h, _ in _EXPORT_COLUMNS])
        for r in records:
            w.writerow([_cell(r, key) for _, key in _EXPORT_COLUMNS])


def export_xlsx(path: str, records: List[Dict]) -> None:
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Usage"
    ws.append([h for h, _ in _EXPORT_COLUMNS])
    for r in records:
        ws.append([_cell(r, key, numeric=True) for _, key in _EXPORT_COLUMNS])
    wb.save(path)


def _cell(r: Dict, key: str, numeric: bool = False):
    v = r.get(key)
    if v is None:
        return "" if not numeric else None
    if numeric and key in _NUMERIC_KEYS:
        return v
    return v
