"""
Headless batch-translate engine for the Supervertaler for Trados large-file
offload (Supervertaler-for-Trados#42 / Workbench#230).

Trados Studio 2024 is a 32-bit process and crashes / runs out of memory on very
large jobs. The plugin hands the work to this 64-bit Workbench, which runs WITHOUT
a GUI (pure Python - no Qt, no window) and hands the result back.

Two headless modes, both detected in Supervertaler.main() BEFORE any QApplication
is created, so neither touches the GUI:

  # File mode (Design B - preferred): translate a Trados .sdlxliff end to end and
  # write a translated .sdlxliff the plugin swaps back in. Trados does NO heavy
  # work - it just reopens the finished file. Reuses the proven
  # StandaloneSDLXLIFFHandler round-trip (tags preserved via <N> markers).
  supervertaler --translate-sdlxliff <in.sdlxliff> --out <out.sdlxliff> --config <job.json>

  # Segment-list mode (returns a TMX). Kept for flexibility / non-file callers.
  supervertaler --batch <job.json> --out <result.tmx>

`job.json` (config) fields: sourceLang, targetLang, provider, model, baseUrl,
apiKey | settingsPath, httpProxy, systemPrompt, batchSize, maxTokens, scope
("EmptyOnly" | "All"). In segment-list mode it also carries `segments`.

result.json: { "ok": bool, "translated": N, "failed": M, "out": "...", "errors": [...] }

The translation core (build_batch_prompt / parse_batch_response, driven by the
existing LLMClient) is shared by both modes and mirrors
PreTranslationWorker._translate_batch_with_llm.
"""

import argparse
import json
import os
import re
import sys
import traceback


# ── Config / key resolution ──

def _resolve_api_key(cfg):
    """Key from the config (the caller's choice), else Workbench's one reader:
    the shared keys, then a referenced settings.json's api_keys section."""
    key = (cfg.get("apiKey") or "").strip()
    if key:
        return key
    provider = (cfg.get("provider") or "").strip()
    settings_path = cfg.get("settingsPath")
    if not settings_path:
        return ""
    from modules.llm_clients import load_api_keys
    return (load_api_keys(settings_path).get(provider) or "").strip()


def _build_client(cfg):
    """Construct the existing LLMClient from the job config. None on import failure."""
    try:
        from modules.llm_clients import LLMClient
    except Exception as e:
        raise RuntimeError("could not import LLMClient: " + str(e))
    api_key = _resolve_api_key(cfg)
    if not api_key and (cfg.get("provider") not in ("ollama",)):
        problem = None
        if cfg.get("settingsPath"):
            # A key that can't be read on this computer says so (M2).
            from modules.llm_clients import api_key_problem
            problem = api_key_problem(cfg.get("provider") or "", settings_path=cfg.get("settingsPath"))
        raise RuntimeError(problem or "no API key for provider '" + str(cfg.get("provider")) +
                           "' (pass apiKey or settingsPath in the job)")
    return LLMClient(
        api_key=api_key,
        provider=cfg.get("provider") or "openai",
        model=cfg.get("model"),
        max_tokens=int(cfg.get("maxTokens") or 16384),
        base_url=cfg.get("baseUrl"),
        http_proxy=cfg.get("httpProxy"),
    )


# ── Prompt / parse (mirrors PreTranslationWorker._translate_batch_with_llm) ──

def build_batch_prompt(batch, source_lang, target_lang, system_prompt):
    parts = []
    if not system_prompt:
        parts.append(f"Translate the following text segments from {source_lang} to {target_lang}.")
    parts.append(f"**SEGMENTS TO TRANSLATE ({len(batch)} segments):**")
    parts.append("\n⚠️ CRITICAL INSTRUCTIONS:")
    parts.append("1. You must provide EXACTLY one translation per segment")
    parts.append(f"2. You MUST translate ALL {len(batch)} segments")
    parts.append("3. Format: Each translation MUST start with its segment number, a period, then the translation")
    parts.append("4. Line breaks: If the source segment contains line breaks, preserve them in your translation.")
    parts.append("   The number label (e.g. '40.') appears only ONCE at the start; continuation lines have no number.")
    next_rule = 5
    has_inline_tags = any(re.search(r'</?\d+/?>', (seg.get("source") or "")) for seg in batch)
    if has_inline_tags:
        parts.append(
            f"{next_rule}. Inline tags: some segments contain numbered tags like "
            "<1>…</1> (paired) or <2/> (standalone). Keep EVERY tag, with the same "
            "numbers, exactly as written - angle brackets, digits, no spaces. A paired "
            "tag MUST wrap the translated word(s) it wrapped in the source; NEVER output "
            "an empty pair like <1></1> with the text left outside it. Tags may be "
            "reordered to fit natural target-language word order.")
        next_rule += 1
    parts.append(f"{next_rule}. NO explanations, NO commentary, ONLY the numbered translations\n")
    parts.append("**SEGMENTS TO TRANSLATE:**\n")
    for seg in batch:
        parts.append(f"{seg['id']}. {seg['source']}")
    parts.append("\n**YOUR TRANSLATIONS (numbered list):**")
    parts.append("Begin your translations now:")
    return "\n".join(parts)


def parse_batch_response(result, batch):
    out = {}
    if not result:
        return out
    current_id = None
    for line in result.split("\n"):
        m = re.match(r'^(\d+)\.\s*(.*)', line)
        if m:
            current_id = int(m.group(1))
            out[current_id] = m.group(2)
        elif current_id is not None:
            out[current_id] += "\n" + line
    # Blank separator lines between numbered entries get appended as trailing
    # newlines above; strip them (keep genuine internal line breaks).
    for _id in out:
        out[_id] = re.sub(r'\n\s*$', '', out[_id])
    return out


def _translate_items(items, source_lang, target_lang, system_prompt, batch_size,
                     client, self_test, log, batch_label="batch"):
    """Translate [{id, source}] in batches. Returns ({id: target}, errors, usage)."""
    if batch_size <= 0:
        batch_size = 20
    id_to_target = {}
    errors = []
    usage = {"inputTokens": 0, "outputTokens": 0}
    total = len(items)
    total_batches = (total + batch_size - 1) // batch_size
    for bi in range(total_batches):
        batch = items[bi * batch_size: (bi + 1) * batch_size]
        prompt = build_batch_prompt(batch, source_lang, target_lang, system_prompt)
        try:
            if self_test:
                response = "\n".join(f"{seg['id']}. {seg['source']}" for seg in batch)
            else:
                response, u = client.translate_with_usage(
                    text=prompt, source_lang=source_lang, target_lang=target_lang,
                    custom_prompt=prompt, system_prompt=system_prompt,
                    enable_prompt_caching=True)
                if isinstance(u, dict):
                    usage["inputTokens"] += int(u.get("input_tokens", 0) or 0)
                    usage["outputTokens"] += int(u.get("output_tokens", 0) or 0)
            parsed = parse_batch_response(response, batch)
            got = 0
            for seg in batch:
                t = parsed.get(seg["id"])
                if t is not None and t.strip():
                    id_to_target[seg["id"]] = t
                    got += 1
            log(f"[{batch_label} {bi + 1}/{total_batches}] {got}/{len(batch)} translated")
        except Exception as e:
            errors.append(f"{batch_label} {bi + 1}: {e}")
            log(f"[{batch_label} {bi + 1}/{total_batches}] ERROR: {e}")
    return id_to_target, errors, usage


# Trados confirmation statuses that count as "finished" (skipped by NotFinalized).
_FINALIZED_STATUSES = ("translated", "approved")


def _segment_in_scope(seg, scope):
    """Whether a SDLSegment should be translated for the given scope."""
    if getattr(seg, "locked", False):
        return False
    if scope == "All":
        return True
    if scope == "NotFinalized":
        # Not Translated + Draft (+ anything not yet confirmed/signed-off).
        return getattr(seg, "status", "not_translated") not in _FINALIZED_STATUSES
    # EmptyOnly (default): only segments with no target text yet.
    return not bool((seg.target_text or "").strip())


# ── File mode (Design B): translate a .sdlxliff round-trip ──

def run_sdlxliff_job(in_path, out_path, cfg, self_test=False, log=print):
    """Translate a Trados .sdlxliff end to end and write a translated .sdlxliff."""
    try:
        from modules.sdlppx_handler import StandaloneSDLXLIFFHandler
    except Exception as e:
        return {"ok": False, "translated": 0, "failed": 0,
                "errors": ["could not import StandaloneSDLXLIFFHandler: " + str(e)]}

    handler = StandaloneSDLXLIFFHandler(log_callback=lambda *_a, **_k: None)
    if not handler.load([in_path]):
        return {"ok": False, "translated": 0, "failed": 0,
                "errors": ["failed to load sdlxliff: " + in_path]}

    scope = (cfg.get("scope") or "EmptyOnly")
    items = []            # [{id, source}]
    id_to_segid = {}      # offload id -> SDLXLIFF segment_id
    next_id = 1
    for xf in handler.xliff_files:
        for seg in xf.segments:
            if not _segment_in_scope(seg, scope):
                continue
            items.append({"id": next_id, "source": seg.source_text or ""})
            id_to_segid[next_id] = seg.segment_id
            next_id += 1
    total_candidates = len(items)

    errors = []
    client = None
    if not self_test and items:
        try:
            client = _build_client(cfg)
        except Exception as e:
            return {"ok": False, "translated": 0, "failed": total_candidates, "errors": [str(e)]}

    src_lang = cfg.get("sourceLang") or "en"
    tgt_lang = cfg.get("targetLang") or "nl"
    sys_prompt = cfg.get("systemPrompt") or None
    bsize = int(cfg.get("batchSize") or 20)

    id_to_target, errs, usage = _translate_items(
        items, src_lang, tgt_lang, sys_prompt, bsize, client, self_test, log)
    errors.extend(errs)

    # Optional retry passes for segments the model left empty.
    if cfg.get("retryUntilComplete") and not self_test:
        max_retries = int(cfg.get("maxRetries") or 3)
        for rp in range(1, max_retries + 1):
            missing = [it for it in items if it["id"] not in id_to_target]
            if not missing:
                break
            log(f"[retry {rp}/{max_retries}] {len(missing)} segment(s) still empty")
            m2, e2, u2 = _translate_items(
                missing, src_lang, tgt_lang, sys_prompt, bsize, client, self_test, log,
                batch_label="retry-batch")
            id_to_target.update(m2)
            usage["inputTokens"] += u2["inputTokens"]
            usage["outputTokens"] += u2["outputTokens"]
            errors.extend(e2)

    translations = {}
    statuses = {}
    for oid, target in id_to_target.items():
        seg_id = id_to_segid.get(oid)
        if seg_id is not None:
            translations[seg_id] = target
            statuses[seg_id] = "draft"

    try:
        handler.update_translations(translations, statuses)
        if len(handler.xliff_files) == 1:
            ok_save = handler.save_file(handler.xliff_files[0], out_path)
            saved = [out_path] if ok_save else []
        else:
            os.makedirs(out_path, exist_ok=True)
            saved = handler.save_all(out_path)
        if not saved:
            errors.append("failed to write translated sdlxliff")
    except Exception as e:
        errors.append("sdlxliff write failed: " + str(e))

    translated = len(translations)
    return {
        "ok": translated > 0 and not errors,
        "translated": translated,
        "failed": total_candidates - translated,
        "out": os.path.abspath(out_path),
        "inputTokens": usage["inputTokens"],
        "outputTokens": usage["outputTokens"],
        "errors": errors,
    }


# ── Segment-list mode: translate [{id, source}] -> TMX ──

def run_job(job, out_tmx, result_path=None, self_test=False, log=print):
    segments = job.get("segments") or []
    source_lang = job.get("sourceLang") or "en"
    target_lang = job.get("targetLang") or "nl"
    errors = []
    client = None
    if not self_test:
        try:
            client = _build_client(job)
        except Exception as e:
            return {"ok": False, "translated": 0, "failed": len(segments), "errors": [str(e)]}

    items = [{"id": s["id"], "source": s.get("source") or ""} for s in segments]
    id_to_target, errs, usage = _translate_items(
        items, source_lang, target_lang, job.get("systemPrompt") or None,
        int(job.get("batchSize") or 20), client, self_test, log)
    errors.extend(errs)

    src_list, tgt_list = [], []
    for seg in segments:
        t = id_to_target.get(seg["id"])
        if t is not None and t.strip():
            src_list.append(seg["source"])
            tgt_list.append(t)
    try:
        from modules.tmx_generator import TMXGenerator
        gen = TMXGenerator(log_callback=lambda *_: None)
        tree = gen.generate_tmx(src_list, tgt_list, source_lang, target_lang)
        os.makedirs(os.path.dirname(os.path.abspath(out_tmx)), exist_ok=True)
        gen.save_tmx(tree, out_tmx)
    except Exception as e:
        errors.append("TMX write failed: " + str(e))

    translated = len(tgt_list)
    res = {"ok": translated > 0 and not errors, "translated": translated,
           "failed": len(segments) - translated, "out": os.path.abspath(out_tmx),
           "inputTokens": usage["inputTokens"], "outputTokens": usage["outputTokens"], "errors": errors}
    if result_path:
        try:
            with open(result_path, "w", encoding="utf-8") as f:
                json.dump(res, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
    return res


# ── CLI ──

def cli_main(argv):
    """Entry point reached from Supervertaler.main() for a headless run."""
    parser = argparse.ArgumentParser(prog="supervertaler (headless)", add_help=True)
    parser.add_argument("--batch", metavar="job.json", help="segment-list job (-> TMX)")
    parser.add_argument("--translate-sdlxliff", dest="sdlxliff", metavar="in.sdlxliff",
                        help="translate a Trados .sdlxliff round-trip (-> translated .sdlxliff)")
    parser.add_argument("--config", metavar="job.json", help="job config for --translate-sdlxliff")
    parser.add_argument("--out", required=True, metavar="out", help="output file (TMX or sdlxliff)")
    parser.add_argument("--result", metavar="result.json", help="write a result summary JSON")
    parser.add_argument("--self-test", action="store_true", help="offline plumbing check (echo sources)")
    args, _ = parser.parse_known_args(argv)

    try:
        if args.sdlxliff:
            cfg = {}
            if args.config:
                with open(args.config, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            res = run_sdlxliff_job(args.sdlxliff, args.out, cfg, self_test=args.self_test)
        elif args.batch:
            with open(args.batch, "r", encoding="utf-8") as f:
                job = json.load(f)
            res = run_job(job, args.out, self_test=args.self_test)
        else:
            print("ERROR: pass --batch or --translate-sdlxliff")
            return 2
    except Exception:
        print("ERROR: headless run crashed:\n" + traceback.format_exc())
        return 1

    if args.result:
        try:
            with open(args.result, "w", encoding="utf-8") as f:
                json.dump(res, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
    print("RESULT " + json.dumps(res, ensure_ascii=False))
    return 0 if res.get("ok") else 1
