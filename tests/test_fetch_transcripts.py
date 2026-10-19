#!/usr/bin/env python3
"""Test script to fetch and print transcriptions from the database."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from storage.database import Database


def main():
    db = Database('chronicle.db')
    db.connect()

    # Get all sessions
    sessions = db.list_sessions()
    if not sessions:
        print("No sessions found in database.")
        return

    print("=" * 60)
    print("Sessions:")
    print("=" * 60)
    for s in sessions:
        print(f"  [{s['id']}] {s['name']} - {s['status']}")

    # Get transcripts for each session
    for session in sessions:
        session_id = session['id']
        print()
        print("=" * 60)
        print(f"Transcripts for Session {session_id}: {session['name']}")
        print("=" * 60)

        transcripts = db.get_transcripts(session_id)
        if not transcripts:
            print("  (no transcripts)")
            continue

        for t in transcripts:
            from datetime import datetime
            ts = datetime.fromtimestamp(t['timestamp']).strftime('%Y-%m-%d %H:%M:%S')
            source = t['source']
            text = t['text']

            print(f"\n[{ts}] [{source}]")
            print(f"  {text[:200]}{'...' if len(text) > 200 else ''}")

    db.disconnect()


if __name__ == '__main__':
    main()