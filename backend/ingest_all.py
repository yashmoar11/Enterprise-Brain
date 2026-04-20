"""
ingest_all.py — Master ingestion script for EnterpriseBrain

Defines which PDF goes into which dataset and runs the full ingestion pipeline.
Each dataset gets its own isolated vector index + full-text index in Neo4j.

Datasets:
  book                    — Hands-On LLM Serving and Optimization
  papers_energy_sustainability — Energy systems, smart grid, carbon footprint, WattDepot
  papers_serious_games    — Kukui Cup, Makahiki, game-based behavior change, PhD dissertation
  papers_hci_ubicomp      — HCI, ubicomp, VR, early CSCW/collaborative systems

Usage:
  python ingest_all.py                        # ingest all datasets
  python ingest_all.py --dataset book         # ingest one specific dataset
  python ingest_all.py --list                 # show all datasets and their files
"""

import os
import sys
import argparse
from ingestion import ingest_document

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

def p(filename):
    """Resolve a filename to its full path in data/."""
    return os.path.join(DATA_DIR, filename)


# ─── Dataset Manifest ────────────────────────────────────────────────────────
#
# Each entry: (file_path, dataset_id, domain)
#
# domain options:
#   "ml"       — ML/software focused entity schema
#   "research" — Academic paper entity schema (Author, Method, Dataset, Result...)
#
MANIFEST = {

    # ── Book ─────────────────────────────────────────────────────────────────
    # Standalone ML/LLM textbook. Completely separate domain from all papers.
    "book": [
        (p("Hands-On LLM Serving and Optimization.pdf"), "book", "ml"),
    ],

    # ── Energy & Sustainability ───────────────────────────────────────────────
    # Papers focused on energy systems, smart grid infrastructure, demand response,
    # carbon footprint measurement, and the WattDepot data platform.
    # Cross-cutting theme: technology infrastructure for energy data and policy.
    "papers_energy_sustainability": [
        # Green lift — demand response potential of elevators in Danish buildings
        (p("1-s2.0-S2214629617301056-main.pdf"),         "papers_energy_sustainability", "research"),
        # Beyond kWh — myths and fixes for energy competition game design
        (p("Beyond_kWh_Myths_and_fixes_for_energy_co.pdf"), "papers_energy_sustainability", "research"),
        # Energy feedback for Smart Grid consumers — lessons from Kukui Cup
        (p("Energy_Feedback_for_Smart_Grid_Consumers.pdf"), "papers_energy_sustainability", "research"),
        # Literature review on carbon footprint collection and analysis
        (p("Literature_Review_on_Carbon_Footprint_Collection_a.pdf"), "papers_energy_sustainability", "research"),
        # WattDepot — open source energy data collection, storage, analysis platform
        (p("WattDepot_An_open_source_software_ecosys.pdf"), "papers_energy_sustainability", "research"),
    ],

    # ── Serious Games & Behavior Change ──────────────────────────────────────
    # Papers focused on game mechanics, gamification, and software systems
    # designed to change energy behavior. Core cluster: Kukui Cup + Makahiki.
    # Includes the PhD dissertation which is the comprehensive study of all this work.
    "papers_serious_games": [
        # PhD dissertation — fostering sustained energy behavior change (comprehensive)
        (p("10-08.pdf"),                                  "papers_serious_games", "research"),
        # Makahiki + WattDepot open source software stack for energy research
        (p("12_06.pdf"),                                  "papers_serious_games", "research"),
        # Tough Shift — casual mobile game for shifting residential electricity use
        (p("2793107.2793108.pdf"),                        "papers_serious_games", "research"),
        # Lights Off, Game On — Kukui Cup dorm energy competition (game design focus)
        (p("Lights_Off_Game_On_The_Kukui_Cup_A_Dorm.pdf"), "papers_serious_games", "research"),
        # Makahiki — open source game engine for energy education and conservation
        (p("Makahiki_An_Open_Source_Game_Engine_for.pdf"), "papers_serious_games", "research"),
        # The Kukui Cup — sustained behavior change and energy literacy
        (p("The_Kukui_Cup_a_Dorm_Energy_Competition.pdf"), "papers_serious_games", "research"),
    ],

    # ── HCI, Ubicomp & Collaborative Systems ─────────────────────────────────
    # Papers focused on human-computer interaction, ubiquitous computing,
    # and early CSCW (computer supported cooperative work) research.
    # Spans ubicomp interfaces, VR, and 1990s collaborative information systems.
    "papers_hci_ubicomp": [
        # Challenge: getting residential users to shift electricity usage (HCI focus)
        (p("2768510.2770934.pdf"),                        "papers_hci_ubicomp", "research"),
        # ClockCast — flexibility of everyday practices for shifting energy (ubicomp)
        (p("3152771.3152803.pdf"),                        "papers_hci_ubicomp", "research"),
        # Automobile Heads Up Display (A-HUD) — traffic and navigation (ubicomp)
        (p("chu_brewer_joseph.pdf"),                      "papers_hci_ubicomp", "research"),
        # Virtual Reality Overlay — US patent (HCI/VR)
        (p("Patent1.pdf"),                                "papers_hci_ubicomp", "research"),
        # Collaborative classification and evaluation of Usenet (CSCW, 1993)
        (p("93-13.pdf"),                                  "papers_hci_ubicomp", "research"),
        # Toward collaborative knowledge management in large info systems (CSCW, 1994)
        (p("Toward_Collaborative_Knowledge_Managemen.pdf"), "papers_hci_ubicomp", "research"),
        # CSRS — instrumented software review system (CSCW, 1993)
        (p("ics-tr-93-19.pdf"),                           "papers_hci_ubicomp", "research"),
    ],
}
# ─────────────────────────────────────────────────────────────────────────────


def list_manifest():
    print("\n=== EnterpriseBrain Dataset Manifest ===\n")
    for dataset_id, files in MANIFEST.items():
        print(f"[{dataset_id}]  ({len(files)} file{'s' if len(files) > 1 else ''})")
        for path, _, domain in files:
            exists = "✓" if os.path.exists(path) else "✗ MISSING"
            print(f"  {exists}  {os.path.basename(path)}  (domain: {domain})")
        print()


def ingest_dataset(dataset_id: str):
    if dataset_id not in MANIFEST:
        print(f"Unknown dataset: '{dataset_id}'")
        print(f"Available: {list(MANIFEST.keys())}")
        sys.exit(1)

    files = MANIFEST[dataset_id]
    print(f"\n{'='*60}")
    print(f"Ingesting dataset: {dataset_id}  ({len(files)} file(s))")
    print(f"{'='*60}")

    for i, (file_path, ds_id, domain) in enumerate(files, 1):
        print(f"\n[{i}/{len(files)}] {os.path.basename(file_path)}")
        if not os.path.exists(file_path):
            print(f"  SKIPPED — file not found: {file_path}")
            continue
        try:
            ingest_document(file_path, dataset_id=ds_id, domain=domain)
        except Exception as e:
            print(f"  ERROR ingesting {os.path.basename(file_path)}: {e}")


def ingest_all():
    print("\n=== Ingesting ALL datasets ===")
    print(f"Datasets: {list(MANIFEST.keys())}\n")
    for dataset_id in MANIFEST:
        ingest_dataset(dataset_id)
    print("\n=== ALL INGESTION COMPLETE ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest documents into EnterpriseBrain")
    parser.add_argument("--dataset", type=str, help="Ingest a specific dataset only")
    parser.add_argument("--list",    action="store_true", help="List all datasets and files")
    args = parser.parse_args()

    if args.list:
        list_manifest()
    elif args.dataset:
        ingest_dataset(args.dataset)
    else:
        ingest_all()
