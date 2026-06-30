# 🗑️ Cullarr

Cullarr is an automated movie management tool that intelligently scores and removes unwanted movies from your Radarr library. It integrates with Plex to track watch history and uses configurable scoring weights to determine which movies should be deleted first.

---

## ✨ Features

- **Smart Scoring Engine** - Movies scored based on age, size, quality, rating, and watch history
- **Plex Integration** - Tracks watched status and recency for smarter deletion decisions
- **Collection Support** - Delete entire TMDB collections together
- **Scheduled Automation** - Cron-based scoring and culling schedules
- **Web Dashboard** - Clean, responsive UI with poster grid view for easy management
- **Dry Run Mode** - Preview changes before making them
- **Plex Collection Sync** - Auto-tag scheduled movies to Plex collections
- **Plex OAuth** - Secure authentication without storing passwords
- **Deletion Staggering** - Spread deletions across multiple days
- **Log Management** - Configurable logging with in-app viewer
- **Deletion History** - Track successful and failed deletions

---

## 📋 Requirements

- **Radarr** (v3 or v4) – must have a working API key
- **Plex Media Server** (optional, but recommended) – for watch history
- **Python 3.10+** (if not using Docker)
- **Docker** (optional, but recommended)

---

## 🚀 Installation

### Using Docker (Recommended)

```bash
# Create a directory for config and logs
mkdir -p /path/to/cullarr-data

# Run the container
docker run -d \
  --name cullarr \
  -p 7447:7447 \
  -v /path/to/cullarr-data:/app/config \
  -v /path/to/cullarr-logs:/app/logs \
  -e LOG_LEVEL=INFO \
  -e LOG_MAX_SIZE_MB=10 \
  -e MAX_LOG_FILES=5 \
  yourusername/cullarr:latest
```

### Docker Compose

```yaml
version: '3.8'

services:
  cullarr:
    image: yourusername/cullarr:latest
    container_name: cullarr
    restart: unless-stopped
    ports:
      - "7447:7447"
    volumes:
      - ./cullarr-data:/app/config
      - ./cullarr-logs:/app/logs
    environment:
      - LOG_LEVEL=INFO
      - LOG_MAX_SIZE_MB=10
      - MAX_LOG_FILES=5
```

### Manual Installation

```bash
git clone https://github.com/yourusername/cullarr.git
cd cullarr
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python -m app.main
```

Access the web interface at `http://localhost:7447`

---

## ⚙️ Configuration

### First Time Setup

1. **Radarr** – Enter your Radarr URL and API key, then click "Test & Save"
2. **Plex** – Add your Plex server URL, then click "Authenticate & Save" to complete OAuth login
3. **Collection** – Select an existing Plex collection where scheduled movies will be added
4. **Scoring Weights** – Adjust the 1-10 sliders to control deletion priorities
5. **Schedules** – Set cron expressions for auto-scoring and auto-culling
6. **Enable** – Toggle the main switch to activate automation

### Scoring Factors

| Factor | Slider Range | Default | Max Contribution | Description |
|--------|-------------|---------|------------------|-------------|
| **Age** | 1-10 | 5 | 2-20% | Movies added longer ago score higher |
| **Size** | 1-10 | 5 | 2-20% | Larger files score higher |
| **Rating** | 1-10 | 5 | 2-20% | Lower TMDB ratings score higher |
| **Quality** | 1-10 | 5 | 2-20% | SD/DVD before 1080p before 4K |
| **Watched** | 1-10 | 5 | 2-20% | Unwatched movies score highest |

> **Note:** Each slider is independent. Slider 10 = 20% weight, Slider 1 = 2% weight. The total weight is the sum of all sliders, which can be less than 100% if not all sliders are at maximum.

### Cron Examples

| Expression | Description |
|------------|-------------|
| `0 3 * * 0` | Every Sunday at 3:00 AM |
| `0 2 * * *` | Every day at 2:00 AM |
| `*/30 * * * *` | Every 30 minutes |
| `0 4 1 * *` | First day of each month at 4:00 AM |

---

## 🎮 Usage

- **Queue Status** – Shows scheduled deletions and system health
- **Run Controls** – Manual scoring or culling with dry run mode
- **Scheduled Deletions** – View pending deletions and due dates
- **Score Queue** – Browse scored movies by deletion priority
- **Deletion History** – Review past deletions and failures

### Dry Run Mode

Enable the **"Dry Run"** checkbox to preview results without making changes.

### Deletion Staggering

Set **"Deletions per day"** to spread deletions across multiple days.

### Protection Rules

- **Protection Days** – New movies have age score of zero
- **Plex Watch History** – Recently watched movies are protected
- **Collections** – Entire collections count as one queue slot

---

## 🔧 API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/radarr/config` | GET/POST/DELETE | Radarr configuration |
| `/api/plex/config` | GET/POST/DELETE | Plex configuration |
| `/api/plex/oauth/pin` | POST | Create Plex OAuth PIN |
| `/api/settings/weights` | GET/POST | Scoring weights |
| `/api/settings` | GET/POST | General settings |
| `/api/run/score` | POST | Trigger score run |
| `/api/run/cull` | POST | Trigger cull run |
| `/api/run/status` | GET | Get running status |
| `/api/dashboard/queue-status` | GET | Queue and health |
| `/api/logs` | GET/DELETE | Log management |

---

## 🛠️ Development

```bash
git clone https://github.com/yourusername/cullarr.git
cd cullarr
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 7447
```

### Project Structure

```
cullarr/
├── app/
│   ├── api/              # FastAPI routers
│   ├── core/             # Business logic
│   ├── db/               # Database layer
│   ├── static/           # JS/CSS assets
│   ├── templates/        # Jinja2 templates
│   ├── utils/            # Helpers
│   └── main.py           # Entry point
└── requirements.txt
```

---

## 📝 Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | `INFO` | DEBUG, INFO, WARNING, ERROR |
| `LOG_MAX_SIZE_MB` | `10` | Max log file size in MB |
| `MAX_LOG_FILES` | `5` | Number of rotated logs to keep |

---

## 🐛 Troubleshooting

**Radarr Connection Fails**
- Verify URL includes `http://` or `https://`
- Check API key is correct
- Ensure Radarr is accessible from Cullarr

**Plex Authentication Fails**
- Ensure Plex URL uses port `32400`
- Allow pop-ups for OAuth window
- Check Plex server allows connections

**No Movies in Score Queue**
- Run a manual score run first
- Verify Radarr has movies with files
- Check scoring weights aren't all minimum

---

## 📄 License

MIT License

---

**Cullarr** – Keep your library fresh, automatically. 🗑️