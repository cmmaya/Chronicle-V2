# Arquitectura de Chronicle V2

## Visión General

Chronicle V2 es una aplicación de escritorio desarrollada en Python que permite grabar reuniones, transcribir audio, capturar pantallas y generar resúmenes automatizados. La aplicación sigue una arquitectura modular basada en componentes que se coordinan a través de un gestor de sesiones central.

---

## Diagrama de Arquitectura

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              APLICACIÓN                                     │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                         src/main.py                                  │   │
│  │                    (Punto de entrada PySide6)                       │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
        ┌───────────────────────────┼───────────────────────────┐
        │                           │                           │
        ▼                           ▼                           ▼
┌───────────────┐         ┌───────────────┐         ┌───────────────┐
│     app       │         │   storage     │         │   assistant   │
│   (UI +       │         │  (Database)   │         │    (AI)       │
│   Sessions)   │         │               │         │               │
└───────────────┘         └───────────────┘         └───────────────┘
        │                           │                           │
        ▼                           │                           ▼
┌───────────────┐                   │                   ┌───────────────┐
│ audio_capture │                   │                   │ summarization │
│  (Recording)  │                   │                   │  (Summary)    │
└───────────────┘                   │                   └───────────────┘
        │                           │
        ▼                           │
┌───────────────┐                   │
│   screenshots │                   │
│   (Capture)   │                   │
└───────────────┘                   │
        │                           │
        ▼                           │
┌───────────────┐                   │
│ transcription │───────────────────┘
│  (Speech-to- │
│    Text)     │
└───────────────┘
```

---

## Módulos

### 1. Módulo `app`

El módulo `app` contiene la lógica de la interfaz de usuario y la gestión del ciclo de vida de las sesiones.

#### Archivos

| Archivo | Descripción |
|---------|-------------|
| `window.py` | Ventana principal de la aplicación. Implementa toda la interfaz gráfica usando PySide6, incluyendo paneles de control de sesiones, transcripción en vivo, y el asistente de IA. |
| `session_manager.py` | Coordina el ciclo de vida completo de las sesiones. Gestiona la creación, inicio, parada de sesiones y la inicialización de componentes como grabadores de audio, capturas de pantalla y procesadores de transcripción. |
| `session.py` | Modelo de datos que representa una sesión de reunión. Mantiene el estado de la sesión (activa, pausada, detenida), tiempos de inicio y fin, y referencias a los componentes de grabación y transcripción. |
| `timeline.py` | Gestiona la línea de tiempo de una sesión, registrando eventos como inicio/fin de audio y capturas de pantalla con sus marcas de tiempo correspondientes. |

#### Relaciones

- `MainWindow` depende de `SessionManager` para todas las operaciones de sesión.
- `SessionManager` crea y administra instancias de `Session`.
- `SessionManager` inicializa los componentes: `DualSourceChunkedRecorder`, `ScreenshotCapture`, y `TranscriptionProcessor`.

---

### 2. Módulo `storage`

El módulo `storage` maneja la persistencia de datos usando SQLite como fuente de verdad.

#### Archivos

| Archivo | Descripción |
|---------|-------------|
| `database.py` | Implementa el acceso a la base de datos SQLite. Gestiona todas las operaciones CRUD (Create, Read, Update, Delete) para sesiones, transcripciones, capturas de pantalla, resúmenes y conversaciones del asistente. |
| `models.py` | Define las estructuras de datos utilizadas en la aplicación. |

#### Esquema de Base de Datos

**Tablas principales:**

- **sessions**: Almacena metadatos de las sesiones (nombre, hora de inicio, estado, estado de transcripción, estado de resumen).
- **transcripts**: Almacena las transcripciones generadas con marca de tiempo, texto y fuente (micrófono/sistema).
- **screenshots**: Almacena las rutas de archivos de capturas de pantalla con marcas de tiempo y descripciones opcionales.
- **summaries**: Almacena los resúmenes generados con el tipo de resumen, contenido y modelo de IA utilizado.
- **assistant_conversations**: Almacena conversaciones del asistente por sesión.
- **assistant_messages**: Almacena los mensajes individuales de cada conversación.

---

### 3. Módulo `audio_capture`

El módulo `audio_capture` maneja la captura de audio del micrófono y del sistema.

#### Archivos

| Archivo | Descripción |
|---------|-------------|
| `core.py` | Implementa `ChunkedAudioRecorder` para grabación en chunks de duración fija (por defecto 10 segundos) y `DualSourceChunkedRecorder` para gestionar ambas fuentes de audio simultáneamente. Usa VAD (Voice Activity Detection) para filtrar audio sin voz. |
| `chunk.py` | Define la clase `AudioChunk` que representa un fragmento de audio grabado, incluyendo metadatos como ID, marcas de tiempo, ruta del archivo y duración. |
| `system_recorder.py` | Implementa la grabación de audio del sistema usando WASAPI loopback de Windows. Permite capturar el audio que se reproduce en el ordenador. |

#### Características

- **Grabación dual**: Captura simultáneamente micrófono y audio del sistema.
- **Chunks solapados**: Usa chunks de 10 segundos con 1 segundo de solapamiento para evitar pérdida de palabras en los bordes.
- **VAD (Voice Activity Detection)**: Filtra chunks que no contienen voz usando WebRTC VAD.
- **Callback de transcripción en vivo**: Notifica a otros componentes cuando un nuevo chunk está disponible para transcripción.

---

### 4. Módulo `audio`

Módulo de bajo nivel para la grabación básica de audio.

#### Archivos

| Archivo | Descripción |
|---------|-------------|
| `recorder.py` | Implementa `AudioRecorder` para capturar audio del micrófono usando sounddevice. Proporciona una interfaz simple para iniciar/detener la grabación. |
| `devices.py` | Proporciona utilidades para listar y seleccionar dispositivos de audio disponibles en el sistema. |

---

### 5. Módulo `transcription`

El módulo `transcription` convierte el audio en texto usando modelos de speech-to-text.

#### Archivos

| Archivo | Descripción |
|---------|-------------|
| `processor.py` | Procesa todos los archivos de audio de una sesión (tanto micrófono como sistema) y genera transcripciones. Coordina el proceso de transcripción completo. |
| `live.py` | Implementa `LiveTranscriber` para la transcripción en tiempo real de chunks de audio durante la grabación. |
| `whisper.py` | Integración con el modelo Whisper para transcripción offline. |
| `parakeet.py` | Integración con el modelo Parakeet V3 para transcripción. |

#### Flujo de Trabajo

1. Durante la grabación, cada chunk de audio se envía a `LiveTranscriber` para transcripción en tiempo real.
2. Al finalizar la sesión, `TranscriptionProcessor` procesa todos los archivos de audio guardados para generar transcripciones completas.
3. Las transcripciones se almacenan en la base de datos con marca de tiempo y fuente.

---

### 6. Módulo `screenshots`

El módulo `screenshots` maneja la captura de pantallas durante las sesiones.

#### Archivos

| Archivo | Descripción |
|---------|-------------|
| `capture.py` | Implementa `ScreenshotCapture` para capturar pantallas completas. Permite capturar la pantalla actual o regiones específicas. |
| `snipping.py` | Proporciona funcionalidad para captura de regiones interactivas, permitiendo al usuario seleccionar un área específica de la pantalla. |

#### Características

- Captura de pantalla completa durante reuniones.
- Captura de regiones interactivas con selección del usuario.
- Almacenamiento de capturas en la carpeta de la sesión con marca de tiempo.
- Registro en la base de datos para sincronización temporal con transcripciones.

---

### 7. Módulo `summarization`

El módulo `summarization` genera resúmenes estructurados de las transcripciones.

#### Archivos

| Archivo | Descripción |
|---------|-------------|
| `generator.py` | Implementa `SummaryGenerator` que usa modelos de IA (a través de OpenRouter) para generar resúmenes de las transcripciones. Genera resúmenes tipo "full", "key_points", "action_items", etc. |
| `templates.py` | Define las plantillas de prompts utilizadas para generar diferentes tipos de resúmenes. |

#### Tipos de Resumen

- **full**: Resumen completo de la reunión.
- **key_points**: Puntos clave discutidos.
- **action_items**: Tareas y acciones asignadas.
- **decisions**: Decisiones tomadas durante la reunión.

---

### 8. Módulo `assistant`

El módulo `assistant` proporciona funcionalidades de asistencia con IA para preguntar sobre las sesiones.

#### Archivos

| Archivo | Descripción |
|---------|-------------|
| `service.py` | Implementa `AssistantAnswerService` que coordina la generación de respuestas a preguntas del usuario. Usa contexto de sesiones y herramientas de búsqueda. |
| `tools.py` | Define las herramientas disponibles para el asistente, como búsqueda de transcripciones y resúmenes. |
| `session_resolver.py` | Resuelve qué sesiones son relevantes para una pregunta dada, determinando si usar la sesión actual o buscar en todas las sesiones. |
| `context.py` | Construye el contexto para el modelo de IA incluyendo transcripciones, resúmenes y metadatos de sesiones relevantes. |
| `context_models.py` | Modelos de datos para representar el contexto del asistente. |
| `openrouter_client.py` | Cliente para comunicarse con la API de OpenRouter, que proporciona acceso a modelos como Gemini Flash y DeepSeek. |

#### Flujo de Trabajo

1. El usuario formula una pregunta en la interfaz.
2. `AssistantAnswerService` determina el alcance (sesión actual o cualquier sesión).
3. `SessionResolver` identifica las sesiones relevantes.
4. `ContextBuilder` recupera transcripciones y resúmenes relevantes.
5. El modelo de IA genera una respuesta basada en el contexto.
6. La respuesta se muestra al usuario y se guarda en la base de datos.

---

### 9. Módulo `config`

El módulo `config` contiene la configuración global de la aplicación.

#### Archivos

| Archivo | Descripción |
|---------|-------------|
| `config.py` | Define constantes y configuraciones globales como agentes disponibles del asistente, configuraciones de VAD, y otras opciones de la aplicación. |

---

## Flujo de Datos

### Grabación de Sesión

```
Usuario inicia sesión
        │
        ▼
SessionManager.create_session()
        │
        ├─▶ Crear directorio de sesión
        ├─▶ Crear registro en DB (status='active')
        ├─▶ Inicializar DualSourceChunkedRecorder
        ├─▶ Inicializar ScreenshotCapture
        └─▶ Inicializar TranscriptionProcessor
        │
        ▼
Usuario inicia grabación
        │
        ▼
DualSourceChunkedRecorder.start()
        │
        ├─▶ Iniciar ChunkedAudioRecorder (mic)
        ├─▶ Iniciar ChunkedAudioRecorder (system)
        │
        ▼
Captura de audio continua
        │
        ├─▶ VAD filtra chunks sin voz
        ├─▶ Guardar chunks en disco
        └─▶ Callback a LiveTranscriber (si está habilitado)
                │
                ▼
                Transcribir chunk (live.py)
                        │
                        ▼
                        Guardar en DB
                                │
                                ▼
                                Notificar UI
        │
        ▼
Usuario captura screenshot
        │
        ▼
ScreenshotCapture.capture_*()
        │
        ├─▶ Guardar imagen en disco
        └─▶ Registrar en DB
        │
        ▼
Usuario detiene grabación
        │
        ▼
SessionManager.stop_session()
        │
        ├─▶ Detener DualSourceChunkedRecorder
        ├─▶ TranscriptionProcessor.process_all()
        │       │
        │       ▼
        │       Transcribir todos los archivos de audio
        │       │
        │       ▼
        │       Guardar transcripciones en DB
        │
        └─▶ Actualizar status en DB
```

### Pregunta al Asistente

```
Usuario envía pregunta
        │
        ▼
MainWindow._on_ask_clicked()
        │
        ▼
AssistantAnswerService.answer_question()
        │
        ├─▶ SessionResolver.resolve_sessions()
        │       │
        │       ▼
        │       Buscar sesiones relevantes
        │
        ├─▶ ContextBuilder.build_context()
        │       │
        │       ▼
        │       Recuperar transcripciones y resúmenes
        │
        └─▶ OpenRouterClient.generate()
                │
                ▼
                Llamar API de OpenRouter
                        │
                        ▼
                        Generar respuesta
                                │
                                ▼
                                Guardar conversación en DB
                                        │
                                        ▼
                                        Mostrar respuesta en UI
```

---

## Dependencias Externas

| Librería | Propósito |
|----------|-----------|
| PySide6 | Interfaz gráfica de usuario |
| sounddevice | Captura de audio del micrófono |
| soundcard | Captura de audio del sistema (WASAPI) |
| webrtcvad | Detección de actividad de voz |
| mss | Captura de pantallas |
| Pillow | Procesamiento de imágenes |
| numpy | Procesamiento de audio |
| sqlite3 | Base de datos local |
| requests | Llamadas HTTP a APIs |

---

## Principios de Diseño

1. **Local-first**: Todos los datos se almacenan localmente. Solo los resúmenes se sincronizan a servicios externos (Notion).
2. **SQLite como fuente de verdad**: La base de datos es la referencia principal para el estado de la aplicación.
3. **Sincronización temporal**: Las transcripciones y capturas de pantalla se vinculan mediante marcas de tiempo para permitir correlación.
4. **Arquitectura modular**: Cada módulo tiene responsabilidad única y se comunica a través de interfaces bien definidas.
5. **Procesamiento asíncrono**: Las operaciones de grabación y transcripción se ejecutan en threads separados para no bloquear la UI.
