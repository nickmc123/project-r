# Agent Onboarding Prompt

Copy and paste this entire section to onboard a new agent to work on these projects.

---

## Required Connections

Before starting, ensure you have access to these connections:

1. **Gmail** (conn_30151wbdacg8fmn2xr4p) - For Authorize.Net emails
2. **Google Drive** (conn_dy5j9pd81jftftfrn83d) - For spreadsheets
3. **GitHub API** (conn_63bfgk3z4yfqa0mvr30t) - For code deployment
4. **Computer Use** (conn_1a2qz61nmtm708yk41qb) - For Railway and manual operations

---

## Copy-Paste Prompt

```
I need you to work on two cash flow management applications. Here are all the details:

## PROJECT 1: CASABLANCA CASH FLOW

**Purpose**: Single-tenant PWA for Casablanca Express travel agency to monitor cash flow.

**URLs**:
- Live App: https://web-production-a76db.up.railway.app
- Access Code: cflownk
- Login: Username `casa`, Password `6300`
- GitHub: https://github.com/nickmc123/cashflow-api

**Tech Stack**: FastAPI + PostgreSQL + Vanilla JS PWA

**Key Business Rules**:
- Daily operations: $9,044/day (refund checks under $1,500 only)
- Payroll: ~$170K/month (twice monthly, 3-day spread)
- Comms & Execs: $51K on 1st, $46K on 15th
- AmEx: $130K twice per month
- CC deposits: $15,836/day; E-deposits: $14,059/day; Wires: $1,907/day
- Always remove first-of-month spike from projections
- Server timezone: Pacific

**Branding**: Orange gradient (#FFA726 to #FF8A65), Casablanca Express logo

---

## PROJECT 2: PROJECT-R (UNIVERSAL CASH FLOW APP)

**Purpose**: Multi-tenant cash flow platform for any business with intelligent categorization and trend analysis.

**URLs**:
- Live App: https://web-production-8d237.up.railway.app
- Demo Account: demo@projectr.app / demo123
- GitHub: https://github.com/nickmc123/project-r
- Railway Project: project-r

**Database** (PostgreSQL):
- Host: shinkansen.proxy.rlwy.net:35334
- User: postgres
- Password: [DB_PASSWORD]
- Database: railway

**Tech Stack**: FastAPI + PostgreSQL + Vanilla JS PWA

**CRITICAL - Repository Structure**:
- Railway serves from ROOT directory, not /app/static/
- Backend files: `/app/main.py`, `/app/models.py`, `/app/services/forecast.py`
- Frontend: `/static/index.html`
- There's also `/api/app/` which is a dev copy - Railway uses `/app/`

**Key Features**:
1. User signup/login with demo account
2. Onboarding flow (company setup, website analyzer, data import)
3. Smart transaction parsing with interactive fallback
4. Transaction groups with categories and frequencies
5. Trend management with adjustable lookback periods
6. Category correlations ("When X goes up, Y goes up by Z%")
7. Cash projections (Daily/Weekly/Monthly with Calculated vs Adjusted toggle)
8. What-if scenarios via natural language chat

**Demo Data**: Acme Coffee Co. with 82 transactions, $47,850 starting balance

---

## PROJECT 3: WISHLIST APP

**Purpose**: Vape cartridge product catalog with wishlist/favorites.

**URLs**:
- Live App: https://wishlist-app-production-0e51.up.railway.app
- Demo Account: demo@wishlist.app / demo123
- GitHub: https://github.com/nickmc123/wishlist-app
- Railway Project: illustrious-nature

**Tech Stack**: FastAPI + SQLite + Vanilla JS PWA
**Theme**: Purple (#6B46C1)

---

## DEPLOYMENT WORKFLOW

### GitHub Push (for all projects):
```python
import base64
import httpx

token = "[GITHUB_TOKEN]"

with open('/path/to/file', 'r') as f:
    content = f.read()

resp = httpx.get(
    f"https://api.github.com/repos/nickmc123/{repo}/contents/{path}",
    headers={"User-Agent": "Tasklet", "Authorization": f"token {token}"}
)
sha = resp.json().get('sha')

httpx.put(
    f"https://api.github.com/repos/nickmc123/{repo}/contents/{path}",
    headers={"User-Agent": "Tasklet", "Authorization": f"token {token}"},
    json={
        "message": "Update description",
        "content": base64.b64encode(content.encode()).decode(),
        "sha": sha
    }
)
```

### For Project-R specifically:
1. Edit `/agent/home/project-r/app/main.py` for backend
2. Edit `/agent/home/project-r/static/index.html` for frontend
3. Push BOTH files to GitHub (app/main.py AND static/index.html)
4. Railway auto-deploys in ~30 seconds

---

## LOCAL FILES

- `/agent/home/cashflow-api/` - Casablanca source
- `/agent/home/project-r/` - Project-R source
- `/agent/home/project-r/app/` - Project-R backend (RAILWAY USES THIS)
- `/agent/home/project-r/static/` - Project-R frontend (RAILWAY USES THIS)
- `/agent/home/wishlist-app/` - Wishlist source
- `/agent/home/PROJECT_DOCUMENTATION.md` - Full documentation

---

## ACTIVE WEBHOOKS

1. **Casablanca Data Updates** (wti_x6gx7ax4z6vwmepgd6th)
   URL: https://webhooks.tasklet.ai/v1/public/webhook?token=[WEBHOOK_TOKEN_1]

2. **Project-R New Signups** (wti_axkvzgrhbp0rhf7rbp2h)
   URL: https://webhooks.tasklet.ai/v1/public/webhook?token=[WEBHOOK_TOKEN_2]
   Sends email to nickmc123@gmail.com on new account creation

---

## TESTING APIS

### Casablanca:
```bash
curl -s -X POST "https://web-production-a76db.up.railway.app/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"username":"casa","password":"6300"}'
```

### Project-R:
```bash
# Demo login
token=$(curl -s -X POST "https://web-production-8d237.up.railway.app/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"email":"demo@projectr.app","password":"demo123"}' | jq -r '.token')

# Get forecast
curl -s "https://web-production-8d237.up.railway.app/forecast?period=weekly&count=4" \
  -H "Authorization: Bearer $token"
```

---

Please read `/agent/home/PROJECT_DOCUMENTATION.md` for complete details on all features, database schemas, and API endpoints.
```

---

*End of onboarding prompt*
