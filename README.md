# Kinetix Backend

Fitness tracker backend (FastAPI + MongoDB + Stripe + Gemini AI).

## Deploy
- **Backend**: Render.com (Docker, plan Starter $7/mes)
- **Frontend**: Netlify (kinetixapp.netlify.app)
- **Database**: MongoDB Atlas (free tier 512MB)

## Local dev
```bash
cd backend
pip install -r requirements.txt
cp .env.example .env  # Y rellena con tus claves
uvicorn server:app --reload --port 8001
```
