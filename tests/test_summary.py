"""Test script to generate and store summary from session_14 transcripts."""
import sqlite3
import sys
import os

# Add src to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Connect to database
conn = sqlite3.connect('chronicle.db')
cursor = conn.cursor()

# Check session 14
cursor.execute('SELECT id, name FROM sessions WHERE id = 23')
session = cursor.fetchone()
print(f"Session: {session}")

# Check existing summaries
cursor.execute('SELECT id, session_id, summary_type, content FROM summaries WHERE session_id = 23')
existing = cursor.fetchall()
print(f"Existing summaries: {len(existing)}")

# Get transcripts
cursor.execute('SELECT id, session_id, source, text FROM transcripts WHERE session_id = 23')
transcripts = cursor.fetchall()
print(f"Transcripts found: {len(transcripts)}")

if transcripts:
    full_text = ' '.join(t[3] for t in transcripts if t[3])
    print(f"Combined text length: {len(full_text)} chars")
else:
    print("No transcripts found")
    exit(1)

conn.close()

# Generate and store summary
print("\n--- Generating and Storing Summary ---")
from src.summarization import SummaryGenerator
from src.storage.database import Database

# Create database and generator
db = Database('chronicle.db')
db.connect()  # Must call connect first
generator = SummaryGenerator(db=db)

# Generate and store
result = generator.generate_and_store(
    transcript=full_text,
    session_id=23,
    summary_type='full'
)

print(f"Model used: {result.get('model_used')}")
print(f"Summary ID: {result.get('summary_id')}")
print(f"\nSummary content:\n{result.get('content')}")

# Verify stored
cursor = db.connection.cursor()
cursor.execute('SELECT id, session_id, summary_type, model_used FROM summaries WHERE session_id = 23')
stored = cursor.fetchall()
print(f"\nStored summaries in DB: {len(stored)}")
for s in stored:
    print(f"  ID: {s[0]}, Session: {s[1]}, Type: {s[2]}, Model: {s[3]}")

db.disconnect()
