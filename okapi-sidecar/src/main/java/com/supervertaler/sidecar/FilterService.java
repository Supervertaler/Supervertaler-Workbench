package com.supervertaler.sidecar;

import net.sf.okapi.common.*;
import net.sf.okapi.common.encoder.EncoderManager;
import net.sf.okapi.common.filters.IFilter;
import net.sf.okapi.common.filters.IFilterConfigurationMapper;
import net.sf.okapi.common.filters.FilterConfigurationMapper;
import net.sf.okapi.common.filterwriter.IFilterWriter;
import net.sf.okapi.common.resource.*;
import net.sf.okapi.common.LocaleId;
import net.sf.okapi.filters.openxml.ConditionalParameters;
import net.sf.okapi.lib.segmentation.SRXDocument;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.*;
import java.net.URI;
import java.nio.file.*;
import java.util.*;
import java.util.regex.*;
import java.util.stream.Collectors;

/**
 * Core service that wraps Okapi Framework filters for extraction, merge,
 * TMX reading/validation, and SRX segmentation.
 */
public class FilterService {

    private static final Logger log = LoggerFactory.getLogger(FilterService.class);

    /** Maps file extensions to Okapi filter configuration IDs. */
    private final IFilterConfigurationMapper fcMapper;

    /** Default SRX document for segmentation. */
    private SRXDocument defaultSrx;

    /** Supported formats: extension → description */
    private static final Map<String, String> SUPPORTED_FORMATS;

    static {
        Map<String, String> m = new LinkedHashMap<>();
        m.put(".docx",  "Microsoft Word 2007+ (OpenXML)");
        m.put(".xlsx",  "Microsoft Excel 2007+ (OpenXML)");
        m.put(".pptx",  "Microsoft PowerPoint 2007+ (OpenXML)");
        m.put(".html",  "HTML");
        m.put(".htm",   "HTML");
        m.put(".xliff", "XLIFF 1.2");
        m.put(".xlf",   "XLIFF 1.2");
        m.put(".tmx",   "TMX (Translation Memory eXchange)");
        m.put(".po",    "PO (Gettext)");
        m.put(".idml",  "Adobe InDesign Markup (IDML)");
        SUPPORTED_FORMATS = Collections.unmodifiableMap(m);
    }

    public FilterService() {
        // Set up the filter configuration mapper with all bundled filters
        fcMapper = new FilterConfigurationMapper();
        fcMapper.addConfigurations("net.sf.okapi.filters.openxml.OpenXMLFilter");
        fcMapper.addConfigurations("net.sf.okapi.filters.tmx.TmxFilter");
        fcMapper.addConfigurations("net.sf.okapi.filters.html.HtmlFilter");
        fcMapper.addConfigurations("net.sf.okapi.filters.xliff.XLIFFFilter");
        fcMapper.addConfigurations("net.sf.okapi.filters.po.POFilter");
        fcMapper.addConfigurations("net.sf.okapi.filters.idml.IDMLFilter");

        // Load the default SRX rules bundled with Okapi
        loadDefaultSrx();

        log.info("FilterService initialised — {} filters available", SUPPORTED_FORMATS.size());
    }

    /**
     * Applies Supervertaler's preferred filter parameters to a freshly
     * created filter. For the Word/OpenXML filter this excludes drawing
     * "graphic metadata" — the auto-generated object names such as
     * "Shape 16", "Group 574248", "Rectangle 4" and picture alt-text —
     * which Okapi otherwise emits as standalone translatable text units.
     * Those are not real content and just clutter the grid (a manual with
     * many diagrams can produce hundreds of them).
     *
     * MUST be applied identically on the extract and merge paths so the
     * text-unit set (and therefore the id↔translation mapping) stays in
     * sync for the round-trip.
     */
    private boolean optBool(Map<String, Object> opts, String key, boolean def) {
        if (opts == null) return def;
        Object v = opts.get(key);
        if (v == null) return def;
        if (v instanceof Boolean) return (Boolean) v;
        return Boolean.parseBoolean(String.valueOf(v));
    }

    /**
     * Applies Supervertaler's preferred filter parameters to a freshly
     * created filter. For the Word/OpenXML filter this controls which
     * parts of an Office document become translatable text units —
     * comments, hidden text, headers/footers, document properties, etc.
     * — plus the exclusion of drawing "graphic metadata" (auto-generated
     * object names such as "Shape 16", "Group 574248").
     *
     * The {@code opts} map carries the user's per-file-type import choices
     * (keys below); any key that is absent falls back to the Supervertaler
     * default. {@code opts} may be null (→ all defaults).
     *
     * MUST be applied identically on the extract and merge paths so the
     * text-unit set (and therefore the id↔translation mapping) stays in
     * sync for the round-trip.
     */
    private void applyFilterParameters(IFilter filter, String configId,
                                       Map<String, Object> opts) {
        if (!"okf_openxml".equals(configId)) {
            return;
        }
        try {
            ConditionalParameters params = new ConditionalParameters();

            // ── Word ──────────────────────────────────────────────
            // true = EXCLUDE graphic metadata (shape/group names, alt-text)
            params.setTranslateWordExcludeGraphicMetaData(
                    optBool(opts, "word_exclude_shapes", true));
            params.setTranslateComments(
                    optBool(opts, "word_comments", false));
            params.setTranslateWordHidden(
                    optBool(opts, "word_hidden", false));
            params.setTranslateWordHeadersFooters(
                    optBool(opts, "word_headers_footers", true));
            params.setTranslateDocProperties(
                    optBool(opts, "word_doc_properties", false));
            params.setAutomaticallyAcceptRevisions(
                    optBool(opts, "word_accept_revisions", true));

            // ── Excel ─────────────────────────────────────────────
            params.setTranslateExcelHidden(
                    optBool(opts, "excel_hidden", false));
            params.setTranslateExcelSheetNames(
                    optBool(opts, "excel_sheet_names", false));
            params.setTranslateExcelDrawings(
                    optBool(opts, "excel_drawings", true));

            // ── PowerPoint ────────────────────────────────────────
            params.setTranslatePowerpointNotes(
                    optBool(opts, "ppt_notes", false));
            params.setTranslatePowerpointComments(
                    optBool(opts, "ppt_comments", false));
            params.setTranslatePowerpointHidden(
                    optBool(opts, "ppt_hidden", false));
            params.setTranslatePowerpointMasters(
                    optBool(opts, "ppt_masters", true));

            filter.setParameters(params);
        } catch (Exception e) {
            // Non-fatal: fall back to default parameters if an option
            // is unavailable in this Okapi build.
            log.warn("Could not apply OpenXML filter parameters: {}", e.getMessage());
        }
    }

    public String getOkapiVersion() {
        // Okapi doesn't have a simple version API, so we read from the JAR manifest
        try {
            Package pkg = IFilter.class.getPackage();
            String v = pkg != null ? pkg.getImplementationVersion() : null;
            return v != null ? v : "unknown";
        } catch (Exception e) {
            return "unknown";
        }
    }

    public List<Map<String, String>> getSupportedFilters() {
        List<Map<String, String>> result = new ArrayList<>();
        for (var entry : SUPPORTED_FORMATS.entrySet()) {
            Map<String, String> item = new LinkedHashMap<>();
            item.put("extension", entry.getKey());
            item.put("description", entry.getValue());
            result.add(item);
        }
        return result;
    }

    // ═══════════════════════════════════════════════════════════════
    //  EXTRACT — Parse a document and return source segments
    // ═══════════════════════════════════════════════════════════════

    public ExtractResult extract(Path filePath, String filename,
                                 String srcLang, String trgLang,
                                 boolean doSegmentation,
                                 Map<String, Object> options) {

        String configId = getConfigIdForFile(filename);
        LocaleId sourceLocale = LocaleId.fromString(srcLang);
        LocaleId targetLocale = LocaleId.fromString(trgLang);

        List<ExtractedSegment> segments = new ArrayList<>();
        int textUnitCount = 0;

        // Track the current sub-document context (body, header, footer, etc.)
        String currentSubDoc = "body";

        IFilter filter = fcMapper.createFilter(configId);
        if (filter == null) {
            throw new IllegalArgumentException(
                    "No Okapi filter found for configuration: " + configId);
        }
        applyFilterParameters(filter, configId, options);

        try {
            RawDocument rawDoc = new RawDocument(filePath.toUri(), "UTF-8", sourceLocale, targetLocale);
            filter.open(rawDoc);

            while (filter.hasNext()) {
                Event event = filter.next();

                // Track sub-document boundaries (headers, footers, etc.)
                EventType etype = event.getEventType();
                if (etype == EventType.START_SUBDOCUMENT) {
                    IResource res = event.getResource();
                    if (res instanceof StartSubDocument) {
                        StartSubDocument ssd = (StartSubDocument) res;
                        currentSubDoc = ssd.getName() != null
                                ? ssd.getName() : "subdocument";
                        log.debug("Entering sub-document: {}", currentSubDoc);
                    }
                } else if (etype == EventType.END_SUBDOCUMENT) {
                    log.debug("Leaving sub-document: {}", currentSubDoc);
                    currentSubDoc = "body";
                }

                if (event.isTextUnit()) {
                    ITextUnit tu = event.getTextUnit();
                    if (!tu.isTranslatable()) continue;

                    textUnitCount++;
                    String tuId = tu.getId();
                    String tuName = tu.getName() != null ? tu.getName() : "";

                    if (doSegmentation && defaultSrx != null) {
                        // Segment the source content using SRX rules
                        // Returns [plainText, taggedText] pairs
                        List<String[]> segs = segmentTextContentWithTags(
                                tu.getSource(), srcLang);
                        for (int i = 0; i < segs.size(); i++) {
                            String[] pair = segs.get(i);
                            segments.add(new ExtractedSegment(
                                    tuId, i, pair[0], pair[1],
                                    tu.getType(), tu.isReferent(),
                                    tuName, currentSubDoc));
                        }
                    } else {
                        // Return the full text unit as a single segment
                        TextFragment tf = tu.getSource().getFirstContent();
                        String plain = tf.toText();
                        String tagged = convertCodesToTags(tf);
                        // Only include tagged version if different from plain
                        String taggedResult = tagged.equals(plain) ? null : tagged;
                        segments.add(new ExtractedSegment(
                                tuId, 0, plain, taggedResult,
                                tu.getType(), tu.isReferent(),
                                tuName, currentSubDoc));
                    }
                }
            }
        } finally {
            filter.close();
        }

        ExtractResult result = new ExtractResult();
        result.filename = filename;
        result.sourceLang = srcLang;
        result.targetLang = trgLang;
        result.filterUsed = configId;
        result.textUnitCount = textUnitCount;
        result.segmentCount = segments.size();
        result.segments = segments;
        return result;
    }

    // ═══════════════════════════════════════════════════════════════
    //  MERGE — Re-create the original document with translations
    // ═══════════════════════════════════════════════════════════════

    public byte[] merge(Path originalPath, String filename,
                        String srcLang, String trgLang,
                        List<MergeSegment> translations,
                        Map<String, Object> options) {

        String configId = getConfigIdForFile(filename);
        LocaleId sourceLocale = LocaleId.fromString(srcLang);
        LocaleId targetLocale = LocaleId.fromString(trgLang);

        // Build a lookup: textUnitId → list of translated segments (ordered)
        Map<String, List<MergeSegment>> txMap = new LinkedHashMap<>();
        for (MergeSegment seg : translations) {
            txMap.computeIfAbsent(seg.id, k -> new ArrayList<>()).add(seg);
        }

        IFilter filter = fcMapper.createFilter(configId);
        if (filter == null) {
            throw new IllegalArgumentException(
                    "No Okapi filter found for configuration: " + configId);
        }
        applyFilterParameters(filter, configId, options);

        // Prepare output
        Path outputPath;
        try {
            outputPath = Files.createTempFile("okapi-merge-out-", getSuffix(filename));
        } catch (IOException e) {
            throw new RuntimeException("Failed to create temp output file", e);
        }

        try {
            RawDocument rawDoc = new RawDocument(originalPath.toUri(), "UTF-8",
                    sourceLocale, targetLocale);
            filter.open(rawDoc);

            IFilterWriter writer = filter.createFilterWriter();
            writer.setOptions(targetLocale, "UTF-8");
            writer.setOutput(outputPath.toString());

            String lastTuId = "<none>";
            String lastTuSourceText = "";
            String lastTuTargetText = "";
            String lastTuCodes = "";
            while (filter.hasNext()) {
                Event event = filter.next();

                if (event.isTextUnit()) {
                    ITextUnit tu = event.getTextUnit();

                    List<MergeSegment> segs = txMap.get(tu.getId());
                    if (segs != null && !segs.isEmpty()) {
                        // ── Per-segment target population ─────────────
                        // Earlier versions concatenated all segment
                        // translations and called target.setContent(...)
                        // which collapses the multi-segment target into
                        // a single segment. That broke the OOXML writer's
                        // run-properties stack on SRX-segmented TUs.
                        // Now we preserve the original segmentation and
                        // set each target segment individually using the
                        // matching source segment's codes for context.
                        TextContainer target = tu.createTarget(
                                targetLocale, false, IResource.COPY_ALL);
                        ISegments srcSegments = tu.getSource().getSegments();
                        ISegments tgtSegments = target.getSegments();
                        int totalSegs = srcSegments.count();

                        StringBuilder combinedDiagnostic = new StringBuilder();
                        StringBuilder codeDump = new StringBuilder();

                        for (MergeSegment mergeSeg : segs) {
                            int idx = mergeSeg.segmentIndex;
                            if (idx < 0 || idx >= totalSegs) continue;
                            if (mergeSeg.translation == null) continue;

                            Segment srcSeg = srcSegments.get(idx);
                            Segment tgtSeg = tgtSegments.get(idx);
                            if (srcSeg == null || tgtSeg == null) continue;

                            TextFragment srcContent = srcSeg.getContent();
                            TextFragment newContent = buildTargetFragment(
                                    mergeSeg.translation, srcContent);
                            tgtSeg.setContent(newContent);

                            // Per-segment diagnostic dump (only used if
                            // the writer throws below).
                            if (combinedDiagnostic.length() > 0) {
                                combinedDiagnostic.append(" | ");
                            }
                            combinedDiagnostic.append("[seg ")
                                    .append(idx).append("] ")
                                    .append(mergeSeg.translation);
                            if (srcContent != null) {
                                for (Code c : srcContent.getCodes()) {
                                    codeDump.append(String.format(
                                            "[seg=%d id=%d type=%s tagType=%s data=%s] ",
                                            idx, c.getId(), c.getType(),
                                            c.getTagType(), c.getData()));
                                }
                            }
                        }

                        lastTuId = tu.getId();
                        lastTuSourceText = tu.getSource().toString();
                        lastTuTargetText = combinedDiagnostic.toString();
                        lastTuCodes = codeDump.toString();
                    }
                }

                try {
                    writer.handleEvent(event);
                } catch (RuntimeException e) {
                    log.error("Merge failed at TU id={}", lastTuId);
                    log.error("  Source first-segment text: {}", lastTuSourceText);
                    log.error("  Source first-segment codes: {}", lastTuCodes);
                    log.error("  Combined translation: {}", lastTuTargetText);
                    throw e;
                }
            }

            writer.close();
            filter.close();

            return Files.readAllBytes(outputPath);
        } catch (IOException e) {
            throw new RuntimeException("Merge failed: " + e.getMessage(), e);
        } finally {
            try { Files.deleteIfExists(outputPath); } catch (IOException ignored) {}
        }
    }

    // ═══════════════════════════════════════════════════════════════
    //  TMX READ — Parse a TMX file and return translation units
    // ═══════════════════════════════════════════════════════════════

    public TmxReadResult readTmx(Path tmxPath) {
        LocaleId sourceLocale = LocaleId.fromString("en");
        // TMX files define their own languages internally; we supply a
        // dummy target locale so the RawDocument constructor is satisfied.
        LocaleId targetLocale = LocaleId.fromString("fr");

        IFilter filter = fcMapper.createFilter("okf_tmx");
        if (filter == null) {
            throw new IllegalStateException("TMX filter not available");
        }

        TmxReadResult result = new TmxReadResult();
        result.translationUnits = new ArrayList<>();

        try {
            RawDocument rawDoc = new RawDocument(tmxPath.toUri(), "UTF-8",
                    sourceLocale, targetLocale);
            filter.open(rawDoc);

            while (filter.hasNext()) {
                Event event = filter.next();

                if (event.isTextUnit()) {
                    ITextUnit tu = event.getTextUnit();
                    TmxTranslationUnit tmxTu = new TmxTranslationUnit();
                    tmxTu.id = tu.getId();

                    // Source
                    if (tu.getSource() != null) {
                        tmxTu.source = TextFragment.getText(
                                tu.getSource().getCodedText());
                    }

                    // All target languages
                    tmxTu.targets = new LinkedHashMap<>();
                    for (LocaleId loc : tu.getTargetLocales()) {
                        TextContainer tc = tu.getTarget(loc);
                        if (tc != null) {
                            tmxTu.targets.put(loc.toString(),
                                    TextFragment.getText(tc.getCodedText()));
                        }
                    }

                    // Properties (metadata)
                    tmxTu.properties = new LinkedHashMap<>();
                    for (String propName : tu.getPropertyNames()) {
                        Property prop = tu.getProperty(propName);
                        if (prop != null) {
                            tmxTu.properties.put(propName, prop.getValue());
                        }
                    }

                    result.translationUnits.add(tmxTu);
                }
            }
        } finally {
            filter.close();
        }

        result.tuCount = result.translationUnits.size();
        result.filename = tmxPath.getFileName().toString();
        return result;
    }

    // ═══════════════════════════════════════════════════════════════
    //  TMX VALIDATE — Check a TMX file for structural issues
    // ═══════════════════════════════════════════════════════════════

    public TmxValidationResult validateTmx(Path tmxPath) {
        TmxValidationResult result = new TmxValidationResult();
        result.filename = tmxPath.getFileName().toString();
        result.issues = new ArrayList<>();

        LocaleId sourceLocale = LocaleId.fromString("en");
        LocaleId targetLocale = LocaleId.fromString("fr");

        IFilter filter = fcMapper.createFilter("okf_tmx");
        if (filter == null) {
            result.valid = false;
            result.issues.add(new TmxIssue("error",
                    "TMX filter not available", 0));
            return result;
        }

        int tuCount = 0;
        int emptySource = 0;
        int emptyTarget = 0;
        Set<String> languages = new LinkedHashSet<>();

        try {
            RawDocument rawDoc = new RawDocument(tmxPath.toUri(), "UTF-8",
                    sourceLocale, targetLocale);
            filter.open(rawDoc);

            while (filter.hasNext()) {
                Event event = filter.next();

                if (event.isTextUnit()) {
                    ITextUnit tu = event.getTextUnit();
                    tuCount++;

                    // Check for empty source
                    if (tu.getSource() == null ||
                        tu.getSource().getCodedText().isBlank()) {
                        emptySource++;
                        result.issues.add(new TmxIssue("warning",
                                "Empty source in TU " + tu.getId(), tuCount));
                    }

                    // Check targets
                    for (LocaleId loc : tu.getTargetLocales()) {
                        languages.add(loc.toString());
                        TextContainer tc = tu.getTarget(loc);
                        if (tc == null || tc.getCodedText().isBlank()) {
                            emptyTarget++;
                            result.issues.add(new TmxIssue("warning",
                                    "Empty target (" + loc + ") in TU " + tu.getId(),
                                    tuCount));
                        }
                    }
                }
            }

            result.valid = result.issues.stream()
                    .noneMatch(i -> "error".equals(i.level));
            result.tuCount = tuCount;
            result.languages = new ArrayList<>(languages);
            result.emptySourceCount = emptySource;
            result.emptyTargetCount = emptyTarget;

        } catch (Exception e) {
            result.valid = false;
            result.issues.add(new TmxIssue("error",
                    "Parse error: " + e.getMessage(), tuCount));
        } finally {
            filter.close();
        }

        return result;
    }

    // ═══════════════════════════════════════════════════════════════
    //  SEGMENT — Segment text using Okapi's SRX engine
    // ═══════════════════════════════════════════════════════════════

    public List<String> segment(String text, String language) {
        if (defaultSrx == null) {
            // Fallback: return as single segment
            return List.of(text);
        }

        ISegmenter segmenter = defaultSrx.compileLanguageRules(
                LocaleId.fromString(language), null);

        TextContainer tc = new TextContainer(text);
        segmenter.computeSegments(tc);
        tc.getSegments().create(segmenter.getRanges());

        List<String> result = new ArrayList<>();
        for (Segment seg : tc.getSegments()) {
            String segText = seg.getContent().toText();
            if (!segText.isBlank()) {
                result.add(segText);
            }
        }
        return result;
    }

    // ── Internal helpers ─────────────────────────────────────────

    /**
     * Segment the text content of a TextContainer using SRX rules.
     * Returns a list of [plainText, taggedText] pairs.
     * taggedText includes HTML-like formatting tags; null if no formatting.
     */
    private List<String[]> segmentTextContentWithTags(TextContainer source, String lang) {
        if (defaultSrx == null || source == null) {
            String text = source != null
                    ? TextFragment.getText(source.getCodedText())
                    : "";
            String tagged = source != null
                    ? convertCodesToTags(source.getFirstContent())
                    : "";
            if (text.isBlank()) return new ArrayList<>();
            String taggedResult = tagged.equals(text) ? null : tagged;
            List<String[]> fallback = new ArrayList<>();
            fallback.add(new String[]{text, taggedResult});
            return fallback;
        }

        ISegmenter segmenter = defaultSrx.compileLanguageRules(
                LocaleId.fromString(lang), null);
        segmenter.computeSegments(source);
        source.getSegments().create(segmenter.getRanges());

        List<String[]> result = new ArrayList<>();
        for (Segment seg : source.getSegments()) {
            TextFragment tf = seg.getContent();
            String plain = TextFragment.getText(tf.getCodedText());
            if (!plain.isBlank()) {
                String tagged = convertCodesToTags(tf);
                String taggedResult = tagged.equals(plain) ? null : tagged;
                result.add(new String[]{plain, taggedResult});
            }
        }
        return result;
    }

    /**
     * Load the default SRX segmentation rules that ship with Okapi.
     */
    private void loadDefaultSrx() {
        try {
            // Try loading the default SRX from Okapi's bundled resources
            InputStream is = getClass().getResourceAsStream(
                    "/net/sf/okapi/lib/segmentation/defaultSegmentation.srx");
            if (is == null) {
                // Alternative path
                is = SRXDocument.class.getResourceAsStream("defaultSegmentation.srx");
            }
            if (is != null) {
                defaultSrx = new SRXDocument();
                defaultSrx.loadRules(is);
                is.close();
                log.info("Default SRX segmentation rules loaded");
            } else {
                log.warn("Default SRX rules not found — segmentation disabled");
            }
        } catch (Exception e) {
            log.warn("Failed to load SRX rules: {}", e.getMessage());
            defaultSrx = null;
        }
    }

    // ═══════════════════════════════════════════════════════════════
    //  Inline code → HTML tag conversion
    // ═══════════════════════════════════════════════════════════════

    /**
     * Convert inline codes in a TextFragment to HTML-like tags.
     * Maps OOXML run properties (bold, italic, underline, etc.) to
     * simple HTML tags like {@code <b>}, {@code <i>}, {@code <u>}.
     *
     * @param fragment the TextFragment containing coded text
     * @return text with HTML formatting tags, or plain text if no
     *         recognisable formatting was found
     */
    private String convertCodesToTags(TextFragment fragment) {
        if (fragment == null) return "";

        List<Code> codes = fragment.getCodes();
        if (codes == null || codes.isEmpty()) {
            return fragment.toText();
        }

        String codedText = fragment.getCodedText();

        // Pre-analyse opening codes to determine the tags for each one.
        // Recognised OOXML formatting (bold/italic/underline/colour) maps to
        // friendly HTML names like <b>, <i>, <cf>. Anything else – HTML
        // hyperlinks, buttons, spans, or any non-formatting inline code we
        // don't have a special analyser for – falls back to a generic
        // <gN>...</gN> pair using the code's id. Without this fallback, the
        // function used to return fragment.toText() and the LLM would see
        // raw Okapi placeholders like [#$dp13] which then leaked into the
        // merged output as literal text. The merge path knows how to round
        // trip <gN>/<xN/> tags back to source codes via tagNamesForCode().
        // Map: code ID → [openingTags, closingTags]
        Map<Integer, String[]> codeIdTags = new HashMap<>();

        for (Code code : codes) {
            if (code.getTagType() == TextFragment.TagType.OPENING) {
                // Try raw XML data first (works for filters that embed OOXML)
                String[] tags = analyzeFormatting(code.getData());
                // Fall back to Okapi's type descriptor (e.g. "x-bold;fonts:Arial;")
                if (tags[0].isEmpty()) {
                    tags = analyzeCodeType(code.getType());
                }
                if (tags[0].isEmpty()) {
                    // Generic fallback: opaque numbered tag pair keyed by id.
                    int id = code.getId();
                    tags = new String[]{"<g" + id + ">", "</g" + id + ">"};
                }
                codeIdTags.put(code.getId(), tags);
            }
        }

        // Build the tagged text
        StringBuilder result = new StringBuilder();
        ArrayDeque<String> openTagStack = new ArrayDeque<>();

        int i = 0;
        while (i < codedText.length()) {
            char ch = codedText.charAt(i);
            if (TextFragment.isMarker(ch) && i + 1 < codedText.length()) {
                int codeIndex = TextFragment.toIndex(codedText.charAt(i + 1));
                if (codeIndex >= 0 && codeIndex < codes.size()) {
                    Code code = codes.get(codeIndex);
                    TextFragment.TagType tagType = code.getTagType();
                    if (tagType == TextFragment.TagType.OPENING) {
                        String[] tags = codeIdTags.get(code.getId());
                        if (tags != null) {
                            result.append(tags[0]);
                            openTagStack.push(tags[1]);
                        }
                    } else if (tagType == TextFragment.TagType.CLOSING) {
                        String[] tags = codeIdTags.get(code.getId());
                        if (tags != null) {
                            result.append(tags[1]);
                            if (!openTagStack.isEmpty()) openTagStack.pop();
                        }
                    } else {
                        // PLACEHOLDER (e.g. <br>, <img>, void elements that
                        // become standalone codes). Emit <xN/> so the LLM
                        // can preserve it positionally; the merge path will
                        // round-trip it back to the original source code.
                        result.append("<x").append(code.getId()).append("/>");
                    }
                }
                i += 2;
            } else {
                result.append(ch);
                i++;
            }
        }

        // Auto-close any unclosed tags (can happen at segment boundaries)
        while (!openTagStack.isEmpty()) {
            result.append(openTagStack.pop());
        }

        return result.toString().trim();
    }

    /**
     * Analyse an Okapi code's data (raw XML) to determine formatting.
     *
     * @param data the raw XML data from the Code
     * @return String[2]: [0] = opening HTML tags, [1] = closing HTML tags
     */
    private String[] analyzeFormatting(String data) {
        if (data == null || data.isEmpty()) return new String[]{"", ""};

        String d = data.toLowerCase();
        StringBuilder open = new StringBuilder();
        StringBuilder close = new StringBuilder();

        // OOXML run properties
        if (hasOoxmlFormat(d, "b"))  { open.append("<b>");   close.insert(0, "</b>");   }
        if (hasOoxmlFormat(d, "i"))  { open.append("<i>");   close.insert(0, "</i>");   }
        if (d.contains("<w:u ") && !d.contains("w:val=\"none\"")) {
            open.append("<u>"); close.insert(0, "</u>");
        }
        if (d.contains("\"superscript\"")) {
            open.append("<sup>"); close.insert(0, "</sup>");
        }
        if (d.contains("\"subscript\"")) {
            open.append("<sub>"); close.insert(0, "</sub>");
        }
        // Strikethrough
        if (hasOoxmlFormat(d, "strike")) {
            open.append("<s>"); close.insert(0, "</s>");
        }

        // HTML-based formats (for HTML filter)
        if (d.contains("<b>") || d.contains("<strong>")) {
            if (open.indexOf("<b>") < 0) { // avoid duplicates
                open.append("<b>"); close.insert(0, "</b>");
            }
        }
        if (d.contains("<i>") || d.contains("<em>")) {
            if (open.indexOf("<i>") < 0) {
                open.append("<i>"); close.insert(0, "</i>");
            }
        }

        return new String[]{open.toString(), close.toString()};
    }

    /**
     * Analyse an Okapi Code's type descriptor for formatting information.
     * The OpenXML filter sets type strings like "x-bold;fonts:Arial;" or
     * "x-bold;x-italic;color:FF0000;fonts:Calibri;".
     *
     * @param codeType the Code.getType() value
     * @return String[2]: [0] = opening tags, [1] = closing tags
     */
    private String[] analyzeCodeType(String codeType) {
        CodeFormat f = CodeFormat.parse(codeType);
        StringBuilder open = new StringBuilder();
        StringBuilder close = new StringBuilder();

        // Only wrap with <cf> when there is meaningful visual info like
        // color.  Font name alone is usually the paragraph default and just
        // adds noise.  When there IS a color, include the font for context.
        if (f.color != null) {
            open.append("<cf color=\"#").append(f.color).append("\"");
            if (f.font != null) {
                open.append(" font=\"").append(f.font).append("\"");
            }
            open.append(">");
        }
        for (String tag : f.tags) {
            open.append('<').append(tag).append('>');
        }
        for (int k = f.tags.size() - 1; k >= 0; k--) {
            close.append("</").append(f.tags.get(k)).append('>');
        }
        if (f.color != null) {
            close.append("</cf>");
        }
        return new String[]{open.toString(), close.toString()};
    }

    /** Formatting tags in nesting order, outermost first. */
    private static final String[] FORMAT_TAG_ORDER = {"b", "i", "u", "s", "sup", "sub"};

    /**
     * What an Okapi inline code's type descriptor says about formatting.
     *
     * <p>Okapi's OpenXML filter (1.47) writes the descriptor with a single
     * {@code x-} prefix and {@code ;}-separated properties, e.g.
     * {@code x-bold;italic;fonts:Arial;underline:single;sub;color:FF0000;}.
     * Earlier code here expected the prefix on every property and long
     * names ({@code x-bold;x-italic;x-underlined;x-subscript}), so only the
     * first property was ever recognised: bold+italic lost its italic, and
     * underline, subscript and superscript became anonymous {@code <gN>}
     * tags. Both spellings are accepted.</p>
     *
     * <p>Extract ({@link #analyzeCodeType}) and merge
     * ({@link #tagNamesForCode}) both read formatting through this one
     * parser, so the tags written out and the tags mapped back cannot
     * drift apart.</p>
     *
     * <p>Hyperlinks keep their earlier rendering (a {@code <cf>} carrying
     * the link colour, or {@code <gN>}): no formatting tags are derived
     * for them.</p>
     */
    static final class CodeFormat {
        final List<String> tags = new ArrayList<>();
        String color;   // hex digits, lower case, no '#'
        String font;
        boolean link;

        static CodeFormat parse(String codeType) {
            CodeFormat f = new CodeFormat();
            if (codeType == null || codeType.isEmpty()) return f;
            Set<String> found = new HashSet<>();
            for (String part : codeType.toLowerCase().split(";")) {
                String p = part.trim();
                if (p.startsWith("x-")) p = p.substring(2);
                if (p.isEmpty()) continue;
                int colon = p.indexOf(':');
                String name = colon >= 0 ? p.substring(0, colon).trim() : p;
                String value = colon >= 0 ? p.substring(colon + 1).trim() : "";
                switch (name) {
                    case "bold":          found.add("b"); break;
                    case "italic":        found.add("i"); break;
                    case "underline":
                    case "underlined":    if (!"none".equals(value)) found.add("u"); break;
                    case "strikethrough": found.add("s"); break;
                    case "sup":
                    case "superscript":   found.add("sup"); break;
                    case "sub":
                    case "subscript":     found.add("sub"); break;
                    case "color":         if (!value.isEmpty()) f.color = value; break;
                    case "fonts":         if (!value.isEmpty()) f.font = value; break;
                    case "link":          f.link = true; break;
                    default:              break;
                }
            }
            if (!f.link) {
                for (String tag : FORMAT_TAG_ORDER) {
                    if (found.contains(tag)) f.tags.add(tag);
                }
            }
            return f;
        }
    }

    // ═══════════════════════════════════════════════════════════════
    //  MERGE — reconstruct inline codes from display tags
    // ═══════════════════════════════════════════════════════════════

    /** Regex that matches our display tags. Covers:
     *   <ul>
     *     <li>formatting tags this code understands: {@code <b>}, {@code </b>},
     *         {@code <cf color="#FF0000">}, {@code </cf>}</li>
     *     <li>placeholder tags emitted by older sidecar versions and/or other
     *         tools, e.g. {@code <hyperlink1>}, {@code </hyperlink1>},
     *         {@code <tags2/>}, {@code <run1>}, {@code </run1>}. These get
     *         stripped silently in {@link #buildTargetFragment} so they don't
     *         end up as literal text in the merged output.</li>
     *   </ul>
     *  Group 1 = leading {@code /} (closing tag), Group 2 = tag name (may
     *  contain digits/underscores), Group 3 = trailing {@code /} (self-closing).
     */
    private static final Pattern TAG_RE = Pattern.compile(
            "<(/?)([a-z][a-z0-9_-]*)(?:\\s[^>]*)?\\s*(/?)>",
            Pattern.CASE_INSENSITIVE);

    /**
     * Every display-tag name a source Code may appear under in a
     * translation, preferred first: its formatting tags (from Okapi's type
     * descriptor, e.g. "x-bold;italic;" → b, i), {@code cf} when it carries
     * a colour, and the generic numbered {@code gN}. A placeholder is only
     * ever {@code xN}, which is what {@link #convertCodesToTags} writes for
     * every placeholder.
     *
     * <p>All names are accepted on the way back in because projects
     * imported by an older sidecar hold some codes under a different one
     * of them: an underline or subscript as {@code <g3>}, bold+italic as
     * {@code <b>} alone. Those must still export with their formatting.</p>
     */
    private List<String> tagNamesForCode(Code code) {
        if (code.getTagType() == TextFragment.TagType.PLACEHOLDER) {
            return Collections.singletonList("x" + code.getId());
        }
        CodeFormat f = CodeFormat.parse(code.getType());
        List<String> names = new ArrayList<>(f.tags);
        if (f.color != null) names.add("cf");
        names.add("g" + code.getId());
        return names;
    }

    /** An entry on the merge's stack of open tags. A {@code null} codeId
     *  marks the second tag of a code that is already open, such as the
     *  {@code <i>} in {@code <b><i>} for one bold+italic run. */
    private static final class OpenTag {
        final Integer codeId;
        final String name;

        OpenTag(Integer codeId, String name) {
            this.codeId = codeId;
            this.name = name;
        }
    }

    /** Next code id in the queue not already placed, or null. */
    private static Integer pollUnused(Queue<Integer> queue, Set<Integer> used) {
        if (queue == null) return null;
        while (!queue.isEmpty()) {
            Integer id = queue.poll();
            if (!used.contains(id)) return id;
        }
        return null;
    }

    /** Next unplaced code for a tag name: preferred-name codes first. */
    private static Integer takeCode(String tagName,
                                    Map<String, Queue<Integer>> tagQueues,
                                    Map<String, Queue<Integer>> aliasQueues,
                                    Set<Integer> used) {
        Integer id = pollUnused(tagQueues.get(tagName), used);
        return id != null ? id : pollUnused(aliasQueues.get(tagName), used);
    }

    /**
     * True when an opening tag belongs to a code that is already open: the
     * nearest real code on the stack (below any follow-on tags) carries
     * this formatting too, was opened under a different name, and has not
     * had this tag yet. So "<b><i>" for one bold+italic run is one code,
     * while "<b>bold <i>and italic</i></b>" over two runs, or a bold run
     * nested in a bold run, stays two.
     */
    private static boolean isPartOfOpenCode(String tagName, Deque<OpenTag> openStack,
                                            Map<Integer, List<String>> namesById) {
        Set<String> followOns = new HashSet<>();
        for (OpenTag open : openStack) {
            if (open.codeId == null) {
                followOns.add(open.name);
                continue;
            }
            return !tagName.equals(open.name)
                    && !followOns.contains(tagName)
                    && namesById.getOrDefault(open.codeId, Collections.emptyList())
                                .contains(tagName);
        }
        return false;
    }

    /**
     * Build a target {@link TextFragment} from a translation string that
     * may contain HTML display tags ({@code <b>}, {@code <i>}, etc.),
     * mapping them back to the original Okapi inline codes from the source.
     *
     * <p>Positional matching is used: the first {@code <b>} in the
     * translation maps to the first bold code in the source, and so on.</p>
     *
     * @param taggedTranslation translation text with display tags
     * @param sourceContent     the source TextFragment (with inline codes)
     * @return a TextFragment with the translation text and proper codes
     */
    private TextFragment buildTargetFragment(String taggedTranslation,
                                             TextFragment sourceContent) {
        List<Code> srcCodes = (sourceContent != null) ? sourceContent.getCodes() : null;

        if (srcCodes == null || srcCodes.isEmpty()) {
            // No codes in source — strip any tags and return plain text
            return new TextFragment(TAG_RE.matcher(taggedTranslation).replaceAll(""));
        }

        // ── Step 1: classify source codes ──────────────────────────
        // Two indexes are built:
        //
        //   tagQueues  – FIFO of code IDs keyed by formatting tag name
        //                ("b", "i", "u", "cf", …). Used for AI-friendly
        //                tags this client knows how to render.
        //   codesByData – FIFO of Codes keyed by their raw getData()
        //                string ("<hyperlink1>", "</hyperlink1>",
        //                "<tags2/>", "<run1>", …). Used to round-trip
        //                structural OOXML codes that we don't have a
        //                named formatting tag for. Crucial for keeping
        //                hyperlinks in the merged output.
        // A code is queued under its preferred name in tagQueues and under
        // its other names in aliasQueues. A tag takes a code it is the
        // preferred name of before one it is only an alias of, so a lone
        // <i> goes to an italic run, not to an earlier bold+italic one.
        Map<String, Queue<Integer>> tagQueues = new LinkedHashMap<>();
        Map<String, Queue<Integer>> aliasQueues = new LinkedHashMap<>();
        Map<Integer, List<String>> namesById = new HashMap<>();
        Map<Integer, Code> openingById = new LinkedHashMap<>();
        Map<Integer, Code> closingById = new LinkedHashMap<>();
        Map<Integer, Code> placeholderById = new LinkedHashMap<>();
        Map<String, Deque<Code>> codesByData = new LinkedHashMap<>();

        for (Code c : srcCodes) {
            if (c.getTagType() == TextFragment.TagType.OPENING) {
                openingById.put(c.getId(), c);
            } else if (c.getTagType() == TextFragment.TagType.CLOSING) {
                closingById.put(c.getId(), c);
            } else {
                // PLACEHOLDER — track so we can round-trip <xN/> tags
                // emitted by convertCodesToTags.
                placeholderById.put(c.getId(), c);
            }
            if (c.getTagType() != TextFragment.TagType.CLOSING) {
                List<String> names = tagNamesForCode(c);
                namesById.put(c.getId(), names);
                for (int k = 0; k < names.size(); k++) {
                    (k == 0 ? tagQueues : aliasQueues)
                            .computeIfAbsent(names.get(k), x -> new LinkedList<>()).add(c.getId());
                }
            }

            String data = c.getData();
            if (data != null && !data.isEmpty()) {
                codesByData.computeIfAbsent(data, k -> new ArrayDeque<>()).add(c);
            }
        }

        // ── Step 2: walk through the translation, replacing tags ───
        TextFragment result = new TextFragment();
        Deque<OpenTag> openStack = new ArrayDeque<>();   // open tags, innermost first
        Set<Integer> used = new HashSet<>();             // code ids already placed
        Matcher m = TAG_RE.matcher(taggedTranslation);
        int lastEnd = 0;

        while (m.find()) {
            // Append plain text before this tag
            if (m.start() > lastEnd) {
                result.append(taggedTranslation.substring(lastEnd, m.start()));
            }

            boolean isClosing = "/".equals(m.group(1));
            boolean isSelfClosing = "/".equals(m.group(3));
            String tagName = m.group(2).toLowerCase();
            String fullTag = m.group(0);

            // ── Step 2a: round-trip via raw code data (structural codes) ──
            // For tags emitted by Okapi's TextFragment.toText() (hyperlinks,
            // runs, OOXML placeholders) the source has a Code whose
            // getData() equals the literal tag string. Match those first
            // and emit a clone of the source code so the structure
            // (e.g. hyperlink anchors) is preserved end-to-end.
            //
            // Code.clone() preserves all fields (outerData, originalId,
            // mergeable, …) that the OpenXML filter writer needs for
            // proper open/close pairing. Per-segment merge in the caller
            // ensures the codes within each segment are balanced, so the
            // writer's run-properties stack can't underflow.
            Deque<Code> dataQueue = codesByData.get(fullTag);
            if (dataQueue != null && !dataQueue.isEmpty()) {
                Code src = dataQueue.poll();
                result.append(src.clone());
            } else if (isSelfClosing) {
                // Self-closing tag (<xN/>, <br/>, etc.). Look up by tag
                // name first – matches the <xN/> placeholders that
                // convertCodesToTags emits for non-OOXML inline codes.
                Integer codeId = takeCode(tagName, tagQueues, aliasQueues, used);
                Code src = codeId != null ? placeholderById.get(codeId) : null;
                if (src != null) {
                    used.add(codeId);
                    result.append(src.clone());
                }
                // else: AI-hallucinated tag with no matching source
                // code – drop silently so it doesn't end up as
                // literal text in the output.
            } else if (!isClosing) {
                // ── OPENING tag ─────────────────────────────────
                if (isPartOfOpenCode(tagName, openStack, namesById)) {
                    // e.g. the <i> of "<b><i>" for one bold+italic run:
                    // that code is already open, so place nothing.
                    openStack.push(new OpenTag(null, tagName));
                } else {
                    Integer codeId = takeCode(tagName, tagQueues, aliasQueues, used);
                    Code src = codeId != null ? openingById.get(codeId) : null;
                    if (src != null) {
                        used.add(codeId);
                        Code c = new Code(TextFragment.TagType.OPENING,
                                          src.getType(), src.getData());
                        c.setId(codeId);
                        result.append(c);
                        openStack.push(new OpenTag(codeId, tagName));
                    }
                    // else: tag not in source → silently drop it
                }
            } else {
                // ── CLOSING tag ─────────────────────────────────
                // Find the most-recently-opened tag this closes. For
                // properly nested tags this is the top.
                Integer matchedId = null;
                Iterator<OpenTag> it = openStack.iterator();
                while (it.hasNext()) {
                    OpenTag open = it.next();
                    boolean matches = open.codeId == null
                            ? open.name.equals(tagName)
                            : namesById.getOrDefault(open.codeId, Collections.emptyList())
                                       .contains(tagName);
                    if (matches) {
                        matchedId = open.codeId;
                        it.remove();
                        break;
                    }
                }
                if (matchedId != null) {
                    Code src = closingById.get(matchedId);
                    if (src != null) {
                        Code c = new Code(TextFragment.TagType.CLOSING,
                                          src.getType(), src.getData());
                        c.setId(matchedId);
                        result.append(c);
                    }
                }
                // else: unmatched closing tag → silently drop it
            }

            lastEnd = m.end();
        }

        // Append any remaining text after the last tag
        if (lastEnd < taggedTranslation.length()) {
            result.append(taggedTranslation.substring(lastEnd));
        }

        return result;
    }

    /**
     * Check for an OOXML formatting element like {@code <w:b/>}.
     * Handles val="false"/val="0" negation and avoids matching
     * longer tag names (e.g. "b" must not match "bCs").
     */
    private boolean hasOoxmlFormat(String dataLower, String tag) {
        String[] patterns = {
                "<w:" + tag + "/>",   // self-closing
                "<w:" + tag + " ",    // with attributes
                "<w:" + tag + ">"     // opening
        };

        boolean found = false;
        for (String p : patterns) {
            if (dataLower.contains(p)) {
                found = true;
                break;
            }
        }
        if (!found) return false;

        // Check for explicit "false" values
        if (dataLower.contains("<w:" + tag + " w:val=\"false\"") ||
            dataLower.contains("<w:" + tag + " w:val=\"0\"")) {
            return false;
        }

        return true;
    }

    /**
     * Map a filename to an Okapi filter configuration ID.
     */
    private String getConfigIdForFile(String filename) {
        String lower = filename.toLowerCase();
        if (lower.endsWith(".docx"))  return "okf_openxml";
        if (lower.endsWith(".xlsx"))  return "okf_openxml";
        if (lower.endsWith(".pptx"))  return "okf_openxml";
        if (lower.endsWith(".html") || lower.endsWith(".htm"))
                                      return "okf_html";
        if (lower.endsWith(".xliff") || lower.endsWith(".xlf"))
                                      return "okf_xliff";
        if (lower.endsWith(".tmx"))   return "okf_tmx";
        if (lower.endsWith(".po"))    return "okf_po";
        if (lower.endsWith(".idml"))  return "okf_idml";

        throw new IllegalArgumentException(
                "Unsupported file format: " + filename +
                ". Supported: " + String.join(", ", SUPPORTED_FORMATS.keySet()));
    }

    private static String getSuffix(String filename) {
        int dot = filename.lastIndexOf('.');
        return dot > 0 ? filename.substring(dot) : "";
    }
}
