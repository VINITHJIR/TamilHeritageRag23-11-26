"""Level 1B ingestion pipeline for TamilHeritage-GPT.

Stages (see pipeline.py):
    acquire -> extract_lgd / extract_asi -> normalize -> classify -> group
    -> resolve -> enrich_wikidata -> enrich_wikipedia -> chunker -> load -> reports

Every stage is a pure-ish function over plain dict records; intermediate results
are written to server/data/interim/*.jsonl so each step can be audited and re-run
independently. PostgreSQL is only written by lgd (locations) and load.
"""
