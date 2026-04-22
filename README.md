# Cullarr

Cullarr is a standalone tool that automatically identifies and deletes unwanted movies from your Radarr library based on user-configurable scoring weights. It integrates with Plex to use watch history as a scoring factor.

## Features

- **Scoring Engine** - Score movies based on Age, Size, TMDB Rating, Quality, Monitored status, and Plex Watch History
- **Configurable Weights** - User-defined weights that sum to 100%
- **Two-Cron System** - Separate schedules for scoring (identify movies) and culling (delete movies)
- **Queue System** - Movies are scheduled for deletion after X days with configurable queue cap
- **Plex Integration** - Optional Plex connection for watch history (multi-user support with admin token)
- **Plex Labels** - Automatically adds user-defined labels to movies pending deletion
- **Dry Run Mode** - Preview what would be deleted without making changes
- **Docker Ready** - Includes Dockerfile and docker-compose for easy deployment

## Quick Start

### Using Docker Compose

```bash
# Clone the repository
git clone https://github.com/yourusername/cullarr.git
cd cullarr

# Create config directory
mkdir -p config logs

# Start the container
docker-compose up -d
Access the web UI at http://localhost:7447

Configuration Steps
Radarr Connection - Go to Settings → Radarr Connection, enter your Radarr URL and API key

Plex Connection (Optional) - Go to Settings → Plex Connection, enable and enter your Plex URL and admin API key

Scoring Weights - Adjust the 6 scoring factors (must sum to 100%)

Scheduling - Set score and cull cron schedules, queue cap, and deletion delay

Enable Cullarr - Toggle the enable switch and save settings

Scoring Factors
Factor	Default Weight	Description
Age	25%	Days since added to Radarr (unbounded)
Size	25%	File size in GB (unbounded)
TMDB Rating	15%	Lower rating = higher deletion score
Quality	15%	4K=0, 1080p=0.3, 720p=0.6, DVD=0.9, SD=1.0
Monitored	10%	Unmonitored = higher score
Watched	10%	0 plays=1.0, 1=0.8, 2=0.6, 3=0.4, 4=0.2, 5+=0.0
Architecture
text
Score Cron (e.g., Sunday 3AM)
    ↓
Score all movies in Radarr
    ↓
Take top N (up to available queue slots)
    ↓
Add to scheduled_deletions with deletion_date = now + delete_after_days
    ↓
Add Plex label to movies

Cull Cron (e.g., Daily 2AM)
    ↓
Find scheduled_deletions where deletion_date ≤ now
    ↓
Delete from Radarr
    ↓
Record in history
Environment Variables
Variable	Default	Description
TZ	UTC	Server timezone
LOG_LEVEL	INFO	Log level (DEBUG, INFO, WARNING, ERROR)
MAX_LOG_FILES	5	Number of log files to keep
LOG_MAX_SIZE_MB	10	Max log file size before rotation
API Endpoints
Endpoint	Method	Description
/api/radarr/config	GET/POST/DELETE	Radarr configuration
/api/plex/config	GET/POST/DELETE	Plex configuration
/api/settings	GET/POST	Application settings
/api/settings/weights	GET/POST	Scoring weights
/api/run/score	POST	Trigger score run
/api/run/cull	POST	Trigger cull run
/api/dashboard/queue-status	GET	Queue and system status
/api/dashboard/score-queue	GET	Paginated score preview
/api/logs	GET/DELETE	Log viewing and clearing
License
MIT