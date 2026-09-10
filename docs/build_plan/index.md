| BU    | Name                                    | Status    | Depends On          | Next  |
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
| BU096 | Delete Past Conversation From Sidebar (Right-Click) | Pending | none         | none  |
