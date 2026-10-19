# Sistema Distribuido de Reconocimiento Facial para Búsqueda a Gran Escala

Sistema de visión artificial y búsqueda biométrica de alta velocidad en tiempo real para identificación, seguimiento y alerta temprana de personas de interés a través de múltiples fuentes de video (cámaras RTSP, webcams o archivos de video).

---

## 🏗️ Arquitectura del Sistema

El sistema implementa una arquitectura desacoplada basada en microservicios, colas de mensajería y búsqueda vectorial para soportar cargas de inferencia pesadas y consultas submilisegundo en bases de datos con miles o millones de identidades:

```text
 ┌─────────────────────────────────────────────────────────────┐
 │               Frontend Dashboard (React + TS)               │
 │            Puerto: 3000 (Alertas en Tiempo Real)            │
 └──────────────┬───────────────────────────────▲──────────────┘
                │ REST API                      │ WebSockets (/api/ws/alerts)
                ▼                               │
 ┌──────────────────────────────────────────────┴──────────────┐
 │             Backend API Gateway (FastAPI)                   │
 │             Puerto: 8000 | Docs: /docs                      │
 └──────┬───────────────────────┬───────────────────────▲──────┘
        │                       │                       │
        ▼                       ▼                       │
 ┌──────────────┐        ┌──────────────┐               │ Pub/Sub
 │  PostgreSQL  │        │    MinIO     │               │ (alerts_channel)
 │  (Metadatos) │        │  (Storage S3)│               │
 │ Puerto: 5432 │        │ Puerto: 9000 │        ┌──────┴──────┐
 └──────────────┘        └──────────────┘        │    Redis    │
                                                 │ Puerto: 6379│
                                                 └──────▲──────┘
                                                        │
                      ┌─────────────────────────────────┴─────────────┐
                      │ BLPOP (face_events_queue)                     │
                      ▼                                               │ RPUSH
               ┌──────────────┐                                       │
               │Search Service│                                ┌──────┴──────┐
               │    FAISS     │                                │  AI Worker  │
               │ (512D Cosine)│                                │(InsightFace)│
               └──────────────┘                                └──────▲──────┘
                                                                      │ RTSP / Cam
                                                                [Cámara / Video]
```

---

## 🧩 Componentes y Tecnologías

| Componente | Tecnologías | Responsabilidad |
| :--- | :--- | :--- |
| **AI Worker** | OpenCV, InsightFace (RetinaFace + ArcFace 512D), ByteTrack | Decodifica video, detecta rostros, realiza seguimiento temporal (tracking) para evitar detecciones redundantes y extrae embeddings vectoriales normalizados. |
| **Search Service** | FAISS (`IndexIDMap2` + `IndexFlatIP`), Redis, Python 3.11 | Consume eventos biométricos de la cola Redis, ejecuta búsqueda vectorial en < 1 ms y publica alertas si la similitud supera el umbral configurado. |
| **Backend Gateway** | FastAPI, SQLAlchemy, PostgreSQL, Redis, MinIO S3 SDK | API REST para administración de cámaras, personas buscadas y alertas. Persiste eventos y distribuye notificaciones en vivo vía WebSockets. |
| **Frontend Dashboard** | React 19, TypeScript, Vite, CSS moderno, Lucide Icons, Nginx | Centro de control interactivo con recepción de alertas push en vivo, filtros, gestión de sujetos/cámaras y ErrorBoundary resiliente contra fallos. |
| **Redis** | Redis 7 Alpine | Amortiguador de ingesta mediante colas FIFO (`face_events_queue`) y bus de difusión Pub/Sub (`alerts_channel`). |
| **PostgreSQL** | PostgreSQL 16 Alpine | Persistencia de metadatos relacionales (cámaras, historial de alertas, personas objetivo registradas). |
| **MinIO** | MinIO S3 (Chainguard) | Almacenamiento de objetos S3 para fotos de perfil y recortes (crops) de rostros capturados como evidencia de alertas. |

---

## 📁 Estructura del Proyecto

A continuación se detalla la estructura actualizada de archivos y directorios del repositorio:

```text
reconocimiento-facial/
│
├── ai_worker/                     # Módulo de ingesta de video e inferencia de visión artificial
│   ├── tracker/                   # Implementación de seguimiento multiobjeto (ByteTrack)
│   │   ├── basetrack.py           # Clase base de tracking y ciclo de vida de pistas
│   │   ├── byte_tracker.py        # Algoritmo de asociación ByteTrack por similitud de IoU
│   │   ├── kalman_filter.py       # Filtro de Kalman para predicción de movimiento de bboxes
│   │   └── matching.py            # Algoritmos de coincidencia de grafos y cálculo de IoU
│   ├── event_publisher.py         # Publicador de eventos biométricos hacia Redis (RPUSH)
│   ├── schemas.py                 # Esquemas Pydantic del evento facial (FaceDetectionEvent)
│   ├── video_processor.py         # Pipeline integral: VideoCapture -> RetinaFace -> ByteTrack -> ArcFace
│   ├── worker_main.py             # Script ejecutable CLI del AI Worker para ingesta continua
│   ├── test_extractor.py          # Prueba interna de extracción de características
│   ├── test_video_pipeline.py     # Prueba del pipeline con archivos de video sintéticos
│   └── Dockerfile                 # Contenedor Docker para ejecución contenerizada del worker
│
├── search_service/                # Servicio de motor vectorial y consumidor de eventos
│   ├── vector_indexer.py          # Clase FaceSearchEngine con FAISS (IndexFlatIP + IndexIDMap2)
│   ├── service_main.py            # Worker en segundo plano: BLPOP de eventos y matching contra FAISS
│   ├── test_vector_search.py      # Test de rendimiento y precisión del índice FAISS
│   ├── faces.index                # Índice binario serializado de FAISS (vectores 512D)
│   ├── faces.json                 # Metadatos sincronizados de identidades indexadas (ID -> Nombre/Notas)
│   ├── requirements.txt           # Dependencias exclusivas (faiss-cpu, numpy, redis)
│   └── Dockerfile                 # Contenedor Docker del Search Service
│
├── backend/                       # API Gateway REST y Servidor WebSocket (FastAPI)
│   ├── app/                       # Núcleo de la aplicación
│   │   ├── config.py              # Parámetros y variables de entorno (BaseSettings)
│   │   ├── database.py            # Configuración SQLAlchemy (PostgreSQL con fallback a SQLite)
│   │   ├── models.py              # Modelos ORM: Camera, TargetPerson, AlertEvent
│   │   ├── schemas.py             # Esquemas Pydantic de entrada/salida (AlertResponse, SystemStats)
│   │   ├── storage.py             # Cliente MinIO S3 con fallback transparente a disco local
│   │   ├── websocket_manager.py   # Administrador de conexiones y broadcast de WebSockets
│   │   ├── redis_listener.py      # Tarea asíncrona de escucha PubSub en Redis y push a WebSockets
│   │   └── routes/                # Controladores de endpoints REST y WS
│   │       ├── alerts.py          # Endpoints para historial de alertas, cambio de estado y métricas
│   │       ├── cameras.py         # CRUD de cámaras y puntos de captura
│   │       ├── persons.py         # Registro de personas objetivo e indexación automática en FAISS
│   │       └── ws.py              # Endpoint WebSocket /api/ws/alerts
│   ├── uploads/                   # Directorio local de almacenamiento de imágenes y evidencias
│   ├── main.py                    # Entrada de FastAPI, configuración de CORS, rutas y ciclo de vida
│   ├── requirements.txt           # Dependencias del backend (FastAPI, uvicorn, SQLAlchemy, psycopg)
│   └── Dockerfile                 # Contenedor Docker del Backend
│
├── frontend/                      # Dashboard Web SPA (React + TypeScript + Vite)
│   ├── public/                    # Íconos y recursos estáticos
│   ├── src/                       # Código fuente TypeScript y React
│   │   ├── assets/                # Imágenes vectoriales y logotipos
│   │   ├── components/            # Componentes modulares de interfaz
│   │   │   ├── AlertsFeed.tsx     # Transmisión y visualización defensiva de alertas en tiempo real
│   │   │   ├── CamerasView.tsx    # Gestión e inventario de cámaras y streams RTSP
│   │   │   ├── PersonsView.tsx    # Registro de personas buscadas y carga de fotografías
│   │   │   ├── StatsCards.tsx     # Tarjetas superiores de métricas del sistema
│   │   │   └── ErrorBoundary.tsx  # Barrera de contención contra caídas de React (evita pantallas negras)
│   │   ├── hooks/                 # Hooks personalizados
│   │   │   └── useWebSocket.ts    # Conexión persistente WebSocket con reconexión automática
│   │   ├── services/              # Cliente HTTP
│   │   │   └── api.ts             # Llamadas tipadas a la API REST (/api/...)
│   │   ├── App.tsx                # Componente principal con navegación por pestañas y handlers de WS
│   │   ├── index.css              # Estilos globales y paleta de colores oscuro (Dark Cyber Theme)
│   │   ├── main.tsx               # Entrada de React montando el árbol dentro del ErrorBoundary
│   │   └── types.ts               # Tipos TypeScript compartidos (Camera, TargetPerson, AlertEvent)
│   ├── nginx.conf                 # Configuración de Nginx para servir la SPA y proxy inverso a /api/
│   ├── package.json               # Dependencias de npm y scripts de compilación
│   └── Dockerfile                 # Build multi-etapa (Node build -> Nginx Alpine)
│
├── docker-compose.yml             # Orquestación de toda la infraestructura (Redis, Postgres, MinIO, Backend, Frontend, Search)
├── DOCUMENTATION.md               # Documentación técnica exhaustiva de diseño, flujos y APIs
├── PROJECT_CONTEXT.md             # Memoria de diseño técnico y contexto de desarrollo
├── requirements.txt               # Dependencias globales de Python para entorno virtual local
├── test_e2e_pipeline.py           # Script de prueba de integración End-to-End local
└── test_extractor.py              # Script utilitario CLI para probar la extracción biométrica sobre fotos
```

---

## 🚀 Despliegue con Docker Compose (Recomendado)

### 1. Prerrequisitos
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) instalado y en ejecución en Windows/Linux.

### 2. Iniciar todos los servicios
Desde la terminal en la raíz del proyecto:

```powershell
docker compose up -d --build
```

### 3. Verificar el estado de los contenedores
```powershell
docker ps
```

Deberás observar los siguientes contenedores en ejecución:
- `rf_frontend` (Puerto `3000` -> Nginx sirviendo React Dashboard)
- `rf_backend` (Puerto `8000` -> FastAPI Gateway)
- `rf_search_service` (Worker de búsqueda FAISS en segundo plano)
- `rf_postgres` (Puerto `5432` -> Base de datos relacional)
- `rf_redis` (Puerto `6379` -> Broker de eventos y alertas)
- `rf_minio` (Puertos `9000` y `9001` -> Almacenamiento S3 y consola web)

### 4. Accesos Web y Consolas

| Servicio | URL | Credenciales |
| :--- | :--- | :--- |
| **Dashboard de Monitoreo** | [http://localhost:3000](http://localhost:3000) | Acceso directo |
| **Documentación API (Swagger)** | [http://localhost:8000/docs](http://localhost:8000/docs) | Acceso directo |
| **Consola MinIO (Storage S3)** | [http://localhost:9001](http://localhost:9001) | `minioadmin` / `minioadminpassword` |
| **PostgreSQL** | `localhost:5432` | `app_user` / `app_secure_password` (`reconocimiento_facial`) |
| **Redis** | `localhost:6379` | Sin contraseña por defecto |

### 5. Iniciar un AI Worker para captura de video
Para poner a decodificar una cámara web o archivo con el worker:
```powershell
# Iniciar worker sobre la webcam del sistema (fuente 0):
python -m ai_worker.worker_main --source 0 --camera-id CAM_ACCESO_1
```
*(Opcionalmente se puede desplegar dentro del contenedor Docker activando el perfil: `docker compose --profile worker up -d`)*.

---

## 💻 Ejecución Local en Windows (Sin Docker)

Si deseas levantar servicios manualmente en tu entorno Python local:

```powershell
# 1. Crear y activar entorno virtual
python -m venv env
.\env\Scripts\Activate.ps1

# 2. Instalar dependencias
pip install -r requirements.txt

# 3. Iniciar el Backend (con fallback local offline a SQLite si no hay Postgres/Redis)
$env:ENABLE_REDIS="false"
python -m uvicorn backend.main:app --reload --port 8000

# 4. Iniciar el Frontend (en otra consola)
cd frontend
npm install
npm run dev
```

---

## 🧪 Herramientas de Diagnóstico y Pruebas

En la raíz se incluyen dos scripts de prueba independientes:

1. **Prueba rápida de extracción biométrica:**
   ```powershell
   python test_extractor.py ruta/a/foto.jpg
   ```
   Valida que InsightFace y los modelos ONNX `buffalo_l` extraigan correctamente los rostros y generen los vectores ArcFace de 512 dimensiones.

2. **Prueba de integración End-to-End completa:**
   ```powershell
   python test_e2e_pipeline.py
   ```
   Genera video sintético en memoria, procesa los fotogramas con ByteTrack, indexa un sujeto en FAISS, ejecuta la búsqueda vectorial y valida que la coincidencia se detecte con más del 75% de similitud en menos de 2 ms.

---

## 📖 Documentación Detallada

Para consultar la arquitectura en profundidad, especificación detallada de cada módulo, contratos JSON de la API REST / WebSocket, políticas de seguridad y guía de troubleshooting, consulta el archivo [DOCUMENTATION.md](file:///C:/Users/damian/Desktop/reconocimiento-facial/DOCUMENTATION.md).
