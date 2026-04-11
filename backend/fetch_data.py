"""
fetch_data.py — Wikipedia data ingestion for EnterpriseBrain

Pulls articles from Wikipedia using the free MediaWiki API (no key needed),
saves them as .txt files to backend/data/, then runs the ingestion pipeline.

Topics chosen specifically because they generate rich knowledge graphs:
  - AI companies (Person → worksAt → Company)
  - AI models (Organization → created → Product)
  - Relationships between research labs, founders, products

Usage:
    python fetch_data.py           # fetch + ingest all topics
    python fetch_data.py --fetch-only   # just download, don't ingest
    python fetch_data.py --ingest-only  # ingest files already in data/
"""

import os
import sys
import argparse
import urllib.request
import urllib.parse
import json

from dotenv import load_dotenv
load_dotenv()

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

# Topics that produce rich entity graphs.
# Mix of organisations, people, products, and concepts.
TOPICS = [
    "OpenAI",
    "Anthropic",
    "Google DeepMind",
    "NVIDIA",
    "Large language model",
    "Transformer (deep learning architecture)",
    "Sam Altman",
    "Demis Hassabis",
    "Retrieval-augmented generation",
]


def fetch_wikipedia_article(title: str) -> str:
    """
    Fetch the full plain-text content of a Wikipedia article using
    the free MediaWiki REST API — no API key required.
    """
    encoded = urllib.parse.quote(title.replace(" ", "_"))
    url = f"https://en.wikipedia.org/w/api.php?action=query&prop=extracts&explaintext=true&titles={encoded}&format=json"

    req = urllib.request.Request(url, headers={"User-Agent": "EnterpriseBrain/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())

    pages = data["query"]["pages"]
    page = next(iter(pages.values()))

    if "extract" not in page:
        raise ValueError(f"No extract found for '{title}'")

    return page["extract"]


def save_articles():
    os.makedirs(DATA_DIR, exist_ok=True)
    saved = []

    for topic in TOPICS:
        safe_name = topic.replace(" ", "_").replace("/", "_").replace("(", "").replace(")", "")
        out_path = os.path.join(DATA_DIR, f"{safe_name}.txt")

        if os.path.exists(out_path):
            print(f"  [skip] {topic} — already downloaded")
            saved.append(out_path)
            continue

        print(f"  [fetch] {topic} ...")
        try:
            text = fetch_wikipedia_article(topic)
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(f"# {topic}\n\n{text}")
            print(f"         saved → {out_path} ({len(text):,} chars)")
            saved.append(out_path)
        except Exception as e:
            print(f"         ERROR: {e}")

    return saved


def ingest_files(paths: list[str]):
    # Import here so the script can be run as `python fetch_data.py --fetch-only`
    # without requiring Neo4j/LangChain to be installed
    from ingestion import ingest_document

    for path in paths:
        print(f"\n→ Ingesting {os.path.basename(path)} ...")
        try:
            ingest_document(path)
        except Exception as e:
            print(f"  INGESTION ERROR for {path}: {e}")


def main():
    parser = argparse.ArgumentParser(description="Fetch Wikipedia articles and ingest them into Neo4j")
    parser.add_argument("--fetch-only", action="store_true", help="Download articles but skip ingestion")
    parser.add_argument("--ingest-only", action="store_true", help="Ingest existing data/ files without downloading")
    args = parser.parse_args()

    if args.ingest_only:
        paths = [os.path.join(DATA_DIR, f) for f in os.listdir(DATA_DIR) if f.endswith(".txt")]
        if not paths:
            print("No .txt files found in data/ — run without --ingest-only first.")
            sys.exit(1)
        print(f"Ingesting {len(paths)} existing files...")
        ingest_files(paths)
        return

    print("=== Fetching Wikipedia articles ===")
    paths = save_articles()
    print(f"\nFetched {len(paths)} articles.")

    if args.fetch_only:
        print("Skipping ingestion (--fetch-only).")
        return

    print("\n=== Ingesting into Neo4j ===")
    print("Make sure Neo4j is running (docker-compose up neo4j)")
    ingest_files(paths)

    print("\n✓ All done. Open http://localhost:3000 and ask questions!")


if __name__ == "__main__":
    main()
