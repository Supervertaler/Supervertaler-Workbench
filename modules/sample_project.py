"""
Sample project
==============

A small ready-made English → Dutch project that shows what Supervertaler does
at a glance (issue #147): TermLens with glossary terms (including a forbidden
term and a non-translatable), TM matches – exact, and fuzzy with the
differences highlighted – already translated and untranslated segments, and an
inline tag.

``ensure_resources`` creates the sample TM and glossary the first time and
reuses them afterwards, so opening the sample again never duplicates anything.
"""

from typing import Dict, List, Tuple

PROJECT_NAME = "Supervertaler sample project"
PROJECT_ID = 147000147          # fixed, so TM/glossary activation carries over
SOURCE_LANG, TARGET_LANG = "en", "nl"
TM_NAME, TM_ID = "Sample TM (Supervertaler)", "supervertaler_sample_tm"
GLOSSARY_NAME = "Sample glossary (Supervertaler)"

# (source, target, status) – the first two are already done
SEGMENTS: List[Tuple[str, str, str]] = [
    ("HydroFlow 300 – Quick Start Guide", "HydroFlow 300 – Snelstartgids", "confirmed"),
    ("Read these instructions carefully before installing the pump.",
     "Lees deze instructies zorgvuldig voordat u de pomp installeert.", "confirmed"),
    ("Keep this manual for future reference.", "", "not_started"),
    ("Switch off the power supply before opening the pump housing.", "", "not_started"),
    ("The pump housing is made of stainless steel.", "", "not_started"),
    ("Check the pressure gauge every week.", "", "not_started"),
    ("Connect the inlet hose to the water supply.", "", "not_started"),
    ("Tighten the two mounting bolts by hand.", "", "not_started"),
    ("Press <1>Start</1> to switch on the pump.", "", "not_started"),
    ("If the pump makes an unusual noise, stop it immediately.", "", "not_started"),
    ("The maximum operating pressure is 6 bar.", "", "not_started"),
    ("Clean the filter every 500 operating hours.", "", "not_started"),
    ("Contact HydroFlow Service if the problem persists.", "", "not_started"),
    ("Dispose of the pump in accordance with local regulations.", "", "not_started"),
]

# One exact match (segment 3) and fuzzy matches whose differences show up
# highlighted in the match panel (segments 4, 6, 10, 11, 12).
TM_ENTRIES: List[Tuple[str, str]] = [
    ("Read these instructions carefully before installing the pump.",
     "Lees deze instructies zorgvuldig voordat u de pomp installeert."),
    ("Keep this manual for future reference.", "Bewaar deze handleiding voor later gebruik."),
    ("Switch off the power supply before opening the control box.",
     "Schakel de voeding uit voordat u de regelkast opent."),
    ("Check the pressure gauge every month.", "Controleer de manometer elke maand."),
    ("If the pump vibrates, stop it immediately.", "Als de pomp trilt, stop deze dan onmiddellijk."),
    ("The maximum operating pressure is 10 bar.", "De maximale bedrijfsdruk is 10 bar."),
    ("Clean the filter every 250 operating hours.", "Reinig het filter elke 250 bedrijfsuren."),
]

# (source, target, options)
GLOSSARY: List[Tuple[str, str, Dict]] = [
    ("pump", "pomp", {}),
    ("pump housing", "pomphuis", {"notes": "Preferred term."}),
    ("pump housing", "pompbehuizing", {"forbidden": True, "notes": "Do not use – say 'pomphuis'."}),
    ("stainless steel", "roestvrij staal", {}),
    ("power supply", "voeding", {}),
    ("pressure gauge", "manometer", {}),
    ("inlet hose", "toevoerslang", {}),
    ("mounting bolt", "bevestigingsbout", {}),
    ("operating pressure", "bedrijfsdruk", {}),
    ("operating hours", "bedrijfsuren", {}),
    ("filter", "filter", {}),
    ("local regulations", "plaatselijke voorschriften", {}),
    ("HydroFlow", "HydroFlow", {"is_nontranslatable": True, "notes": "Brand name."}),
]


def ensure_resources(db_manager, tm_metadata_mgr, termbase_mgr, project_id: int = PROJECT_ID):
    """Create the sample TM and glossary if they don't exist yet, and switch
    them on for the sample project. Returns ``(tm_id, termbase_id)``."""
    # TM
    tm = tm_metadata_mgr.get_tm_by_tm_id(TM_ID)
    if tm is None:
        tm_db_id = tm_metadata_mgr.create_tm(TM_NAME, TM_ID, source_lang=SOURCE_LANG,
                                             target_lang=TARGET_LANG, auto_unique_id=False,
                                             description="Created by Help → Open Sample Project")
        tm = tm_metadata_mgr.get_tm(tm_db_id) if tm_db_id else None
        if tm:
            for source, target in TM_ENTRIES:
                db_manager.add_translation_unit(source, target, SOURCE_LANG, TARGET_LANG, tm_id=TM_ID)
            tm_metadata_mgr.update_entry_count(TM_ID)
    if tm:
        tm_metadata_mgr.activate_tm(tm['id'], project_id)

    # Glossary
    termbase_id = next((tb['id'] for tb in termbase_mgr.get_all_termbases()
                        if tb.get('name') == GLOSSARY_NAME), None)
    if termbase_id is None:
        termbase_id = termbase_mgr.create_termbase(
            GLOSSARY_NAME, source_lang=SOURCE_LANG, target_lang=TARGET_LANG,
            description="Created by Help → Open Sample Project")
        if termbase_id:
            for source, target, options in GLOSSARY:
                termbase_mgr.add_term(termbase_id, source, target, source_lang=SOURCE_LANG,
                                      target_lang=TARGET_LANG, **options)
    if termbase_id:
        termbase_mgr.set_as_project_termbase(termbase_id, project_id)
    return TM_ID, termbase_id
