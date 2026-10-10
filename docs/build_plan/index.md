﻿| BU    | Name                                    | Status    | Depends On          | Next  |
| ----- | --------------------------------------- | --------- | ------------------- | ----- |
| BU034 | Assistant Conversation Storage          | Pending   | none                | BU035 |
| BU035 | Assistant Agent Options Config          | Pending   | BU034               | BU036 |
| BU036 | Assistant OpenRouter Client             | Pending   | BU035               | BU037 |
| BU037 | Assistant Context Models                | Pending   | BU034               | BU038 |
| BU038 | Single Session Assistant Retrieval      | Pending   | BU037               | BU039 |
| BU039 | Assistant Database Search Methods       | Pending   | BU034               | BU040 |
| BU040 | Assistant Session Resolver              | Completed | BU037, BU039        | BU041 |
| BU041 | Whitelisted Assistant Retrieval Tools   | Completed | BU038, BU039, BU040 | BU042 |
| BU042 | Assistant Answer Service                | Pending   | BU036, BU040, BU041 | BU043 |
| BU043 | Assistant UI Panel Skeleton             | Pending   | BU035               | BU044 |
| BU044 | Wire Assistant Ask Action               | Pending   | BU042, BU043        | BU045 |
| BU045 | Assistant Clarification Flow UI         | Pending   | BU040, BU044        | BU046 |
| BU046 | Detachable Assistant Chat               | Pending   | BU043, BU044        | BU047 |
| BU047 | Past Assistant Conversations UI         | Pending   | BU034, BU044        | none  |
| BU    | Name                                    | Status    | Depends On          | Next  |
| ---   | ---                                     | ---       | ---                 | ---   |
| BU048 | Session-Scoped Conversation Persistence | Pending   | none                | BU049 |
| BU049 | Assistant Context Loader                | Pending   | BU048               | BU050 |
| BU050 | Assistant Conversation Writeback        | Pending   | BU048, BU049        | BU051 |
| BU051 | Automatic Summary Setting               | Pending   | none                | BU052 |
| BU052 | Auto Summary Toggle Button              | Pending   | BU051               | BU053 |
| BU053 | Auto Summary On Stop                    | Pending   | BU051, BU052        | BU054 |
| BU054 | Assistant Model Setting                 | Pending   | none                | BU055 |
| BU055 | Model Selector UI                       | Pending   | BU054               | BU056 |
| BU056 | Apply Selected Model To AI Calls        | Pending   | BU054, BU055        | BU057 |
| BU057 | Screenshot AI Context Field             | Pending   | none                | BU058 |
| BU058 | Screenshot Context Generator            | Pending   | BU057, BU054        | BU059 |
| BU059 | Screenshot Context Button               | Pending   | BU057, BU058        | BU060 |
| BU060 | Pause Resume Session Lifecycle          | Pending   | none                | BU061 |
| BU061 | Pause Button UI                         | Pending   | BU060               | BU062 |
| BU062 | Home Layout Shell                       | Pending   | none                | BU063 |
| BU063 | Session Search Combobox                 | Pending   | BU062               | BU064 |
| BU064 | All Sessions Dropdown Arrow             | Pending   | BU063               | BU065 |
| BU065 | Selected Session Scope Label            | Pending   | BU063               | BU066 |
| BU066 | Session Action Icon Row                 | Pending   | BU062               | BU067 |
| BU067 | Play Stop Icon Wiring                   | Pending   | BU066               | BU068 |
| BU068 | Screenshot Icon Wiring                  | Pending   | BU066, BU067        | BU069 |
| BU069 | Viewer Icon Wiring                      | Pending   | BU066, BU067        | BU070 |
| BU070 | Ongoing Session Split View              | Pending   | BU062               | none  |
| BU071 | Create RAG Metadata Schema              | Pending   | none                |
| BU072 | Add RAG Source Upsert Helpers           | Pending   | BU071               |
| BU073 | Add RAG FTS Rebuild                     | Pending   | BU072               |
| BU074 | Add Unified FTS Search                  | Pending   | BU073               |
| BU075 | Expose Unified Search Tool              | Pending   | BU074               |
| BU076 | Create RAG Result Models                | Pending   | BU075               |
| BU077 | Add Current Session FTS Retrieval       | Pending   | BU076               |
| BU078 | Replace Current Session Context Path    | Pending   | BU077               |
| BU079 | Add Any Session Context Builder         | Pending   | BU075               |
| BU080 | Wire Any Session Service Retrieval      | Pending   | BU079               |
| BU081 | Improve Session Resolver Search Terms   | Pending   | BU074               |
| BU082 | Tighten Chronicle Assistant Prompt      | Pending   | BU080               |
| BU083 | Add Retrieval Tests                     | Pending   | BU080               |
| BU084 | Add Local Embedding Schema Placeholder  | Pending   | BU083               |
| BU085 | Implement RAG Content Indexing          | Pending   | BU084               | BU086 |

Any Session Refactor — see docs/build_plan/ANY_SESSION_REFACTOR.md

| BU    | Name                                    | Status    | Depends On          | Next  |
| ---   | ---                                     | ---       | ---                 | ---   |
| BU086 | Fix RAG FTS MATCH Clause                | Completed | BU085               | BU087 |
| BU087 | Timestamp-Preserving Transcript Chunking| Completed | BU085, BU086        | BU088 |
| BU088 | Scope Mode Indicator And Rename         | Completed | none                | BU089 |
| BU089 | Session Router For Any Session          | Completed | BU086, BU087        | BU090 |
| BU090 | Assistant Answer Contract And Handoff   | Completed | BU088, BU089        | BU091 |
| BU091 | Embedding Backfill And Incremental Reindex | Completed | BU087, BU089    | none  |

Scope Switch Prompt — follow-on to BU090

| BU    | Name                                    | Status    | Depends On          | Next  |
| ---   | ---                                     | ---       | ---                 | ---   |
| BU092 | Scope Switch Offer Decision And Payload | Pending   | BU089, BU090        | BU093 |
| BU093 | In-Chat Scope Switch Prompt             | Pending   | BU092               | none  |

Router Retrieval Fix — independent bug fix, informed by a real repro against production data

| BU    | Name                                    | Status    | Depends On          | Next  |
| ---   | ---                                     | ---       | ---                 | ---   |
| BU094 | Session Router Coverage And Scoring Repair | Pending | BU089, BU091     | none  |

System Audio Capture Resilience — independent bug fix, diagnosed from a real session (session_046) that stopped capturing system audio ~40 min in

| BU    | Name                                    | Status    | Depends On          | Next  |
| ---   | ---                                     | ---       | ---                 | ---   |
| BU095 | System Audio Capture Supervisor And Watchdog | Completed | none           | none  |

Conversation Management — user-requested UI feature

| BU    | Name                                    | Status    | Depends On          | Next  |
| ---   | ---                                     | ---       | ---                 | ---   |
| BU096 | Delete Past Conversation From Sidebar (Right-Click) | Completed | none       | none  |

Keyword Search — user-requested UI feature

| BU    | Name                                    | Status    | Depends On          | Next  |
| ---   | ---                                     | ---       | ---                 | ---   |
| BU097 | Keyword Search For Transcripts And Conversations | Completed | none          | BU098 |
| BU098 | Active Pane Focus Model                 | Completed | none                | BU099 |
| BU099 | In-Pane Find Bar (Ctrl+F) With Match Navigation | Completed | BU097, BU098 | none  |

Summary Window Enhancement — user-requested UI feature

| BU    | Name                                    | Status    | Depends On          | Next  |
| ---   | ---                                     | ---       | ---                 | ---   |
| BU100 | Collapsible Sectioned Summary View      | Completed | none                | none  |

Transcript Download — user-requested UI feature

| BU    | Name                                    | Status    | Depends On          | Next  |
| ---   | ---                                     | ---       | ---                 | ---   |
| BU101 | Transcript Download Button              | Completed | none                | none  |

Resume Session — user-requested UI feature

| BU    | Name                                    | Status    | Depends On          | Next  |
| ---   | ---                                     | ---       | ---                 | ---   |
| BU102 | Resume Stopped Session From All Sessions | Completed | BU060               | none  |

Fluid Transcript Stream — user-requested UI feature

| BU    | Name                                    | Status    | Depends On          | Next  |
| ---   | ---                                     | ---       | ---                 | ---   |
| BU103 | Grouped Live Transcript Bubbles         | Completed | none                | none  |

Upload Audio — user-requested UI feature

| BU    | Name                                    | Status    | Depends On          | Next  |
| ---   | ---                                     | ---       | ---                 | ---   |
| BU104 | Upload Audio File As Session            | Completed | none                | none  |

Performance, Memory, and Threading Rework — user-requested, informed by a walkthrough of the capture/transcribe/store pipeline and a read-only chronicle.db audit

| BU    | Name                                    | Status    | Depends On          | Next  |
| ---   | ---                                     | ---       | ---                 | ---   |
| BU105 | Performance, Memory, and Threading Rework | Completed | none              | none  |

Screenshot Module Overhaul — user-requested: image-viewer UI in the summary theme, and a screenshot-aware Specific Session assistant (preview-first search, then full metadata)

| BU    | Name                                    | Status    | Depends On          | Next  |
| ---   | ---                                     | ---       | ---                 | ---   |
| BU106 | Screenshot Preliminary Description And Metadata Optimization | Completed | none | BU107 |
| BU107 | Two-Tier Screenshot Search Engine       | Completed   | BU106               | BU108 |
| BU108 | Screenshot-Aware Specific Session Answers | Completed | BU107               | BU109 |
| BU109 | Screenshot Viewer Window                | Completed   | BU106, BU108        | BU110 |
| BU110 | Screenshot Extras: Visible-Text Search, Live-Session Viewing, Capture Hotkey | Completed | BU109 | none |

Live Q&A In The Transcripts Window — user-requested: manual chunk selection and auto question detection, informed by a measured detector bake-off on session 46 ("Class 1 - RM P2")

| BU    | Name                                    | Status    | Depends On          | Next  |
| ---   | ---                                     | ---       | ---                 | ---   |
| BU111 | Structured Transcript Records And Detached Bubble Grouping | Completed | none | BU112 |
| BU112 | Detached Window Two-Column Layout And Transcript Polish | Completed | BU111 | BU113 |
| BU113 | Manual Chunk Selection And Answer From Context Menu | Completed | BU111, BU112 | BU114 |
| BU114 | Question Detection Service              | Completed | BU111               | BU115 |
| BU115 | Auto Mode Controls And Detected-Question Cards | Completed | BU113, BU114 | BU116 |

Live Q&A Answer Quality — user-requested after using BU115: auto-mode answers were built from the transcript window without the detected question, and answers were too long

| BU    | Name                                    | Status    | Depends On          | Next  |
| ---   | ---                                     | ---       | ---                 | ---   |
| BU116 | Concise Answers And Two Answer Modes    | Completed | BU115               | BU117 |
| BU117 | Reference .txt File As An Answer Source | Completed | BU116               | none  |

Audio Mute Controls — user-requested: mute the mic or the system audio mid-session from the chat section

| BU    | Name                                    | Status    | Depends On          | Next  |
| ---   | ---                                     | ---       | ---                 | ---   |
| BU118 | Mic And System Audio Mute Toggles       | Completed | none                | none  |

Persisted UI State — user-requested after BU118, informed by an audit of preferences.json

| BU    | Name                                    | Status    | Depends On          | Next  |
| ---   | ---                                     | ---       | ---                 | ---   |
| BU119 | Persisted UI State And A Safe Preferences File | Completed | BU118        | none  |

Installable Windows App — user-requested: install to any folder, with models downloaded by a setup wizard before the app window first appears; informed by a dependency audit of every import

| BU    | Name                                    | Status    | Depends On          | Next  |
| ---   | ---                                     | ---       | ---                 | ---   |
| BU120 | Dependency Audit And Clean Requirements | Completed | none                | BU121 |
| BU121 | App Paths: Separate Install Folder From User Data | Completed | none      | BU122 |
| BU122 | API Key In The Windows Credential Store | Completed | BU121               | BU123 |
| BU123 | Model Manager: Check, Download And Verify Models | Completed | BU121      | BU124 |
| BU124 | First-Run Setup Wizard Before The Main Window | Completed | BU121, BU122, BU123 | BU125 |
| BU125 | Windowed-App Hardening: Log File, Crash Dialog, Single Instance | Completed | BU121 | BU126 |
| BU126 | PyInstaller Build Of A Standalone App Folder | Completed | BU120, BU121, BU125 | BU127 |
| BU127 | Windows Installer With A Choosable Install Folder | Completed | BU124, BU126 | none |

Settings Pop-Up — user-requested: replace the menu bar the Settings button reveals with a pixel-art pop-up window

| BU    | Name                                    | Status    | Depends On          | Next  |
| ---   | ---                                     | ---       | ---                 | ---   |
| BU128 | Settings Pop-Up Window                  | Completed | BU119, BU122        | none  |

Send Due Dates To Google Calendar — user-requested: a per-entry "Send to Calendar" button in the summary's Due Dates, an editable event dialog (all-day when no time is given), and Google sign-in configured from Settings

| BU    | Name                                    | Status    | Depends On          | Next  |
| ---   | ---                                     | ---       | ---                 | ---   |
| BU129 | Machine-Readable Calendar Date In Due Dates Entries | Completed | none      | BU130 |
| BU130 | Google Account Sign-In (OAuth Desktop Flow) | Completed | BU121, BU122      | BU131 |
| BU131 | Calendar Page In The Settings Pop-Up    | Completed | BU128, BU130        | BU132 |
| BU132 | Calendar Event Service And Sent-Event Tracking | Completed | BU129, BU130 | BU133 |
| BU133 | "Send To Calendar" Button And Event Dialog | Completed | BU129, BU131, BU132 | none |

Inserted Text Transcripts — user-requested: an "Upload Transcript" button next to Upload Audio that turns a text file into a summarized session; its transcript is flagged, shows an "Inserted Transcript" caption in both transcript windows, and disables the transcript-only controls

| BU    | Name                                    | Status    | Depends On          | Next  |
| ---   | ---                                     | ---       | ---                 | ---   |
| BU134 | Session Origin Flag For Inserted Transcripts | Completed | none             | BU135 |
| BU135 | Text Transcript File Parser             | Completed | none                | BU136 |
| BU136 | Import A Text Transcript As A Session   | Completed | BU134, BU135        | BU137 |
| BU137 | "Upload Transcript" Button In All Sessions | Completed | BU136              | BU138 |
| BU138 | Inserted Transcript View In The Main Transcripts Panel | Completed | BU137 | BU139 |
| BU139 | Inserted Transcript Mode In The Detached Window | Completed | BU138       | BU140 |
| BU140 | "Text" Label For Inserted Transcripts In Assistant Context | Completed | BU134 | none |

Assistant Answers For Live And Fresh Sessions — user-reported: a question asked right after Stop (or during recording) got 5 unrelated transcript lines and answered "not mentioned"; answers seemed to need the transcript's exact word

| BU    | Name                                    | Status    | Depends On          | Next  |
| ---   | ---                                     | ---       | ---                 | ---   |
| BU141 | Live-Aware Transcript Retrieval         | Completed | none                | BU142 |
| BU142 | Chronological, Timestamped Session Evidence | Completed | BU141           | BU143 |
| BU143 | Search Terms: Stop Words, Stems, Follow-Ups | Completed | BU141           | none  |
| BU144 | Whole Transcript For Specific Session Questions | Completed | BU141, BU142    | none  |
