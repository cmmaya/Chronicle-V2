# Chronicle — Meeting Capture & Knowledge Organization

## Purpose
Local-first desktop application for meeting capture and knowledge organization.

## Core Capabilities
- Record microphone and system audio (Windows WASAPI loopback)
- Transcribe audio streams using Parakeet V3
- Manual screenshots/snipping during meetings
- Store all assets locally in organized session folders
- Generate structured meeting summaries
- Sync lightweight summaries to Notion

## Primary Workflow
record → save → transcribe → screenshot → summarize → notion

## Architecture Principles
- SQLite as source of truth
- Local-first architecture
- Transcripts, screenshots, and audio remain local
- Timestamp synchronization between transcript and screenshots
- Modular implementation

## Tech Stack
- UI: PySide6
- Audio: sounddevice, Windows WASAPI loopback
- Transcription: Parakeet V3
- Screenshots: mss, Pillow
- Summarization: OpenRouter (Gemini Flash/DeepSeek)
- Sync: Notion API (summary-only)
- Storage: SQLite

## Critical Constraints
- No transcript uploads to Notion
- No screenshot uploads to Notion
- Timestamp synchronization required
- Low Builder context
- Stateless Builder sessions